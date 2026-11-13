"""scripts/cloud/lock-watch.sh: what the lock watch proposes and how.

The fakes for gh and curl accept only the calls the script makes and fail the
way GitHub does on anything else (AGENTS.md, "Test fakes"): a ref that exists
can't be created, one that doesn't can't be moved, a commit needs the branch's
current head, and a pull request whose head lands in main is marked merged."""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, git_env
from test_lock import FAKE_CURL, executable

SCRIPT = REPO / "scripts/cloud/lock-watch.sh"

LOCK = (
    "# header\n"
    "apt cmake 3.28.3-1build7\n"
    "apt-snapshot - 20261101T000000Z\n"
    "base - mirror.gcr.io/library/ubuntu:24.04@sha256:aaa\n"
    "brew go 1.27.3\n"
    "claude - 2.1.318\n"
    "rust - 1.99.1\n"
)

FAKE_GH = r'''#!/usr/bin/env python3
import base64, hashlib, json, os, subprocess, sys
state_path = os.environ["FAKE_GH_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
def fail(status, message):
    sys.stderr.write("gh: " + message + " (HTTP " + str(status) + ")\n")
    sys.exit(1)
def save():
    json.dump(state, open(state_path, "w"))
def log(line):
    state.setdefault("log", []).append(line)
if not args or args[0] != "api":
    fail(0, "fake: only `gh api`: " + " ".join(args))
args = args[1:]
method, path, fields, jq, body_from_stdin = "GET", None, {}, None, False
i = 0
while i < len(args):
    a = args[i]
    if a == "-X":
        method = args[i + 1]; i += 2
    elif a in ("-f", "-F"):
        key, _, value = args[i + 1].partition("=")
        if a == "-F" and value.startswith("@"):
            value = open(value[1:]).read()
        elif a == "-F" and value in ("true", "false"):
            value = value == "true"
        fields[key] = value; i += 2
    elif a == "--jq":
        jq = args[i + 1]; i += 2
    elif a == "--input" and args[i + 1] == "-":
        body_from_stdin = True; i += 2
    elif a.startswith("-"):
        fail(0, "unknown flag: " + a)
    elif path is None:
        path = a; i += 1
    else:
        fail(0, "unexpected argument: " + a)
if body_from_stdin:
    if fields:
        fail(0, "fake: --input with fields")
    fields = json.load(sys.stdin)
repo = os.environ["FAKE_REPO"]
refs, pulls, commits = state["refs"], state["pulls"], state["commits"]
def out(value):
    save()
    text = json.dumps(value)
    if jq is not None:
        text = subprocess.run(["jq", "-r", jq], input=text, capture_output=True, text=True, check=True).stdout
        sys.stdout.write(text)
    else:
        print(text)
    sys.exit(0)
def mark_merged():
    for pr in pulls:
        if pr["state"] == "open" and refs.get(pr["head"]) in state["main_history"]:
            pr["state"] = "closed"; pr["merged"] = True
prefix = "repos/" + repo + "/"
if path == "graphql":
    query = fields.get("query", "")
    if "createCommitOnBranch" in query:
        inp = fields["variables"]["input"]
        name = inp["branch"]["branchName"]
        if inp["branch"]["repositoryNameWithOwner"] != repo or name not in refs:
            fail(200, "Could not resolve to a Ref")
        if refs[name] != inp["expectedHeadOid"]:
            fail(200, "Expected branch to point to " + inp["expectedHeadOid"])
        files = dict(commits[refs[name]]["files"])
        for add in inp["fileChanges"]["additions"]:
            files[add["path"]] = base64.b64decode(add["contents"]).decode()
        sha = hashlib.sha1(json.dumps([refs[name], files, inp["message"]], sort_keys=True).encode()).hexdigest()
        commits[sha] = {"parent": refs[name], "files": files, "message": inp["message"]}
        refs[name] = sha
        log("commit " + name + " " + sha)
        out({"data": {"createCommitOnBranch": {"commit": {"oid": sha}}}})
    if "enablePullRequestAutoMerge" in query:
        pr = next((p for p in pulls if p["node_id"] == fields.get("id") and p["state"] == "open"), None)
        if pr is None:
            fail(200, "Could not resolve to a node with the global id of '" + str(fields.get("id")) + "'")
        pr["auto_merge"] = "SQUASH"
        log("auto-merge " + str(pr["number"]))
        out({"data": {"enablePullRequestAutoMerge": {"clientMutationId": None}}})
    fail(200, "fake: unexpected query")
if not path.startswith(prefix):
    fail(404, "Not Found: " + path)
rest = path[len(prefix):]
if rest.startswith("git/ref/heads/") and method == "GET":
    name = rest[len("git/ref/heads/"):]
    if name not in refs:
        fail(404, "Not Found")
    out({"ref": "refs/heads/" + name, "object": {"sha": refs[name]}})
if rest == "git/refs" and method == "POST":
    name = fields["ref"][len("refs/heads/"):]
    if name in refs:
        fail(422, "Reference already exists")
    if fields["sha"] not in commits:
        fail(422, "Object does not exist")
    refs[name] = fields["sha"]; log("create " + name + " " + fields["sha"]); mark_merged()
    out({"ref": fields["ref"]})
if rest.startswith("git/refs/heads/") and method == "PATCH":
    name = rest[len("git/refs/heads/"):]
    if name not in refs:
        fail(422, "Reference does not exist")
    if fields.get("force") is not True:
        fail(422, "Update is not a fast forward")
    refs[name] = fields["sha"]; log("move " + name + " " + fields["sha"]); mark_merged()
    out({"ref": "refs/heads/" + name})
if rest.startswith("git/refs/heads/") and method == "DELETE":
    name = rest[len("git/refs/heads/"):]
    if name not in refs:
        fail(422, "Reference does not exist")
    del refs[name]; log("delete " + name)
    save(); sys.exit(0)
if rest.startswith("pulls?") and method == "GET":
    query = dict(part.split("=", 1) for part in rest[len("pulls?"):].split("&"))
    owner = repo.split("/")[0]
    found = [p for p in pulls if owner + ":" + p["head"] == query["head"] and p["base"] == query["base"] and p["state"] == query["state"]]
    out(found)
if rest == "pulls" and method == "POST":
    if fields["head"] not in refs:
        fail(422, "head: invalid")
    if any(p["head"] == fields["head"] and p["state"] == "open" for p in pulls):
        fail(422, "A pull request already exists for " + fields["head"])
    if refs[fields["head"]] == refs[fields["base"]]:
        fail(422, "No commits between main and " + fields["head"])
    number = len(pulls) + 1
    pr = {"number": number, "node_id": "PR_" + str(number), "head": fields["head"], "base": fields["base"],
          "title": fields["title"], "body": fields["body"], "state": "open"}
    pulls.append(pr); log("open " + str(number))
    out(pr)
if rest.startswith("pulls/") and method == "PATCH":
    pr = next((p for p in pulls if str(p["number"]) == rest[len("pulls/"):]), None)
    if pr is None:
        fail(404, "Not Found")
    for key in fields:
        if key not in ("title", "body", "state"):
            fail(422, "unknown field " + key)
    pr.update(fields); log("edit " + str(pr["number"]) + " " + ",".join(sorted(fields)))
    out(pr)
if rest.startswith("actions/workflows/") and "/runs?" in rest and method == "GET":
    workflow, _, query = rest[len("actions/workflows/"):].partition("/runs?")
    query = dict(part.split("=", 1) for part in query.split("&"))
    if set(query) - {"event", "head_sha"}:
        fail(422, "fake: unknown parameter " + ",".join(sorted(set(query) - {"event", "head_sha"})))
    runs = [r for r in state.get("runs", []) if r["workflow"] == workflow
            and all(r[key] == value for key, value in query.items())]
    out({"total_count": len(runs), "workflow_runs": sorted(runs, key=lambda r: -r["id"])})
if rest.startswith("actions/runs/") and rest.endswith("/rerun-failed-jobs") and method == "POST":
    run = next((r for r in state.get("runs", []) if str(r["id"]) == rest.split("/")[2]), None)
    if run is None:
        fail(404, "Not Found")
    if run["status"] != "completed":
        fail(403, "This workflow is already running")
    if run["conclusion"] not in ("failure", "timed_out", "cancelled"):
        fail(403, "This workflow run has no failed jobs")
    run.update(status="queued", conclusion=None, run_attempt=run["run_attempt"] + 1)
    log("rerun " + str(run["id"]))
    out({})
fail(404, "Not Found: " + method + " " + path)
'''

# Issues on other repositories: `gh api repos/<o>/<r>/issues/<n> --jq .state`.
FAKE_GH_ISSUES = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
issues = json.loads(os.environ.get("FAKE_ISSUES", "{}"))
if len(args) == 4 and args[0] == "api" and args[2:] == ["--jq", ".state"]:
    if args[1] not in issues:
        sys.exit("gh: Not Found (HTTP 404)")
    print(issues[args[1]])
    sys.exit(0)
sys.exit("gh: fake: unexpected call: " + " ".join(args))
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

    def run_watch(self, *args, stdin=None, env=None, cwd=REPO):
        settings = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "GITHUB_REPOSITORY": "lolay/agent-images"}
        settings.pop("GITHUB_RUN_ID", None)
        settings.update(env or {})
        return subprocess.run(["bash", str(SCRIPT), *args], cwd=cwd, input=stdin, env=settings,
                              capture_output=True, text=True, timeout=60)

    def tree(self, name, locks):
        root = self.tmp / name
        for type_, text in locks.items():
            path = root / "images/cloud" / type_ / "versions.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        root.mkdir(exist_ok=True)
        return root


class TypesTests(Base):
    def test_classes(self):
        expected = {"nightly": "linux\n", "weekly": "gis\nlinux-gpu\n", "all": "gis\nlinux\nlinux-gpu\n"}
        for class_, types in expected.items():
            with self.subTest(class_):
                result = self.run_watch("types", class_)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, types, "android is a VM type and has no lock")

    def test_unknown_class(self):
        result = self.run_watch("types", "hourly")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nightly, weekly or all", result.stderr)


class GuardTests(Base):
    def test_locks_pass(self):
        result = self.run_watch("guard", stdin="images/cloud/linux/versions.lock\nimages/cloud/linux-gpu/versions.lock\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_anything_else_fails(self):
        for path in (".github/workflows/cloud-build.yml", "images/cloud/linux/Dockerfile",
                     "images/cloud/../../Makefile/versions.lock", "images/cloud/linux/sub/versions.lock",
                     "images/cloud/linux/versions.lock.bak", "x/images/cloud/linux/versions.lock",
                     "images/cloud/Linux/versions.lock"):
            with self.subTest(path):
                result = self.run_watch("guard", stdin=f"images/cloud/linux/versions.lock\n{path}\n")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"refusing to push {path}", result.stderr)

    def test_nothing_fails(self):
        result = self.run_watch("guard", stdin="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing to push", result.stderr)


class TitleTests(Base):
    def test_one_type(self):
        base = self.tree("base", {"linux": LOCK, "gis": LOCK})
        new = self.tree("new", {"linux": LOCK.replace("claude - 2.1.318", "claude - 2.1.320")
                                .replace("apt cmake 3.28.3-1build7", "apt cmake 3.28.3-1build8"), "gis": LOCK})
        result = self.run_watch("title", str(base), str(new))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "lock(linux): claude 2.1.320, apt 1 package\n")

    def test_several_types(self):
        base = self.tree("base", {"linux": LOCK, "gis": LOCK})
        new = self.tree("new", {"linux": LOCK.replace("brew go 1.27.3", "brew go 1.27.4"),
                                "gis": LOCK.replace("claude - 2.1.318", "claude - 2.1.320")})
        result = self.run_watch("title", str(base), str(new))
        self.assertEqual(result.stdout, "lock(gis, linux): gis: claude 2.1.320; linux: go 1.27.4\n")

    def test_only_a_pin_moved(self):
        base = self.tree("base", {"linux": LOCK})
        new = self.tree("new", {"linux": LOCK + "pinned rust https://github.com/rust-lang/rust/issues/1\n"})
        self.assertEqual(self.run_watch("title", str(base), str(new)).stdout, "lock(linux): pins and snapshot\n")

    def test_a_new_type(self):
        base = self.tree("base", {"linux": LOCK})
        new = self.tree("new", {"linux": LOCK, "gis": LOCK})
        self.assertEqual(self.run_watch("title", str(base), str(new)).stdout,
                         "lock(gis): claude 2.1.318, base mirror.gcr.io/library/ubuntu:24.04@sha256:aaa, rust 1.99.1, go 1.27.3, apt 1 package\n")

    def test_long_titles_are_cut(self):
        brew = "".join(f"brew formula{n} 1.0\n" for n in range(60))
        base = self.tree("base", {"linux": LOCK})
        new = self.tree("new", {"linux": LOCK + brew})
        title = self.run_watch("title", str(base), str(new)).stdout.rstrip("\n")
        self.assertEqual(len(title), 256)
        self.assertTrue(title.endswith("..."))

    def test_nothing_moved_fails(self):
        base = self.tree("base", {"linux": LOCK})
        result = self.run_watch("title", str(base), str(base))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no lock moved", result.stderr)

    def test_body_shows_each_diff(self):
        base = self.tree("base", {"linux": LOCK})
        new = self.tree("new", {"linux": LOCK.replace("claude - 2.1.318", "claude - 2.1.320")})
        body = self.run_watch("body", str(base), str(new), env={"GITHUB_RUN_ID": "7"}).stdout
        self.assertIn("([run](https://github.com/lolay/agent-images/actions/runs/7))", body)
        self.assertIn("### linux\n\n```text\nclaude/-: 2.1.318 -> 2.1.320\n```\n", body)


class PinsTests(Base):
    def setUp(self):
        super().setUp()
        executable(self.bin / "curl", FAKE_CURL)
        executable(self.bin / "gh", FAKE_GH_ISSUES)
        (self.tmp / "urls.json").write_text(json.dumps({
            "https://downloads.claude.ai/claude-code-releases/latest": "2.1.320",
            "https://static.rust-lang.org/dist/channel-rust-stable.toml": '[pkg.rust]\nversion = "1.99.1 (x 2026-10-13)"\n',
            "redirect https://github.com/terraform-linters/tflint/releases/latest":
                "https://github.com/terraform-linters/tflint/releases/tag/v0.66.0",
        }))
        self.env = {"FAKE_URLS": str(self.tmp / "urls.json"), "FAKE_LOG": str(self.tmp / "log"),
                    "FAKE_ISSUES": json.dumps({"repos/anthropics/claude-code/issues/9": "closed",
                                               "repos/rust-lang/rust/issues/1": "open"})}

    def pins(self, locks):
        result = self.run_watch("pins", str(self.tree("t", locks)), env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_each_pin_with_upstream_and_its_issue(self):
        out = self.pins({
            "linux": LOCK.replace("rust - 1.99.1", "rust - 1.91.0")
            + "pinned claude https://github.com/anthropics/claude-code/issues/9\n"
            + "pinned rust https://github.com/rust-lang/rust/issues/1\n"
            + "release tflint v0.60.0\npinned tflint -\n"
            + "pinned apt/cmake https://bugs.launchpad.net/ubuntu/+bug/1\n",
        })
        self.assertIn("| linux | claude | `2.1.318` | `2.1.320` | https://github.com/anthropics/claude-code/issues/9 **closed**: remove the pin? |", out)
        self.assertIn("| linux | rust | `1.91.0` | `1.99.1` | https://github.com/rust-lang/rust/issues/1 open |", out)
        self.assertIn("| linux | tflint | `v0.60.0` | `v0.66.0` |  no issue named |", out)
        self.assertIn("| linux | apt/cmake | `3.28.3-1build7` | `-` | https://bugs.launchpad.net/ubuntu/+bug/1 not a GitHub issue |", out)

    def test_an_unreadable_issue_says_so(self):
        out = self.pins({"linux": LOCK + "pinned claude https://github.com/anthropics/claude-code/issues/404\n"})
        self.assertIn("issues/404 unreadable |", out)

    def test_no_pins(self):
        self.assertEqual(self.pins({"linux": LOCK, "gis": LOCK}), "### Pins\n\nNo pins.\n")

    def test_a_failed_upstream_lookup_fails_the_list(self):
        (self.tmp / "urls.json").write_text("{}")
        result = self.run_watch("pins", str(self.tree("t", {"linux": LOCK + "pinned claude -\n"})), env=self.env)
        self.assertNotEqual(result.returncode, 0)


class GitTests(Base):
    """carry and proposed, against a scratch repo with main and lock/watch."""

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env={**git_env(), "GIT_AUTHOR_NAME": "t",
                              "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
                              check=True, capture_output=True, text=True).stdout.strip()

    def write(self, type_, text):
        path = self.repo / "images/cloud" / type_ / "versions.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        for name in ("infra/gcp", "scripts/cloud", "hooks/gcp"):
            shutil.copytree(REPO / name, self.repo / name)
        self.git("init", "-q", "-b", "main")
        for type_ in ("linux", "gis", "linux-gpu"):
            self.write(type_, LOCK)
        self.git("add", "-A")
        self.git("commit", "-qm", "main")
        self.git("checkout", "-qb", "lock/watch")
        self.write("gis", LOCK.replace("2.1.318", "2.1.320"))
        self.write("linux", LOCK.replace("2.1.318", "2.1.319"))
        self.git("commit", "-qam", "candidate")
        self.git("checkout", "-q", "main")

    def run_git_watch(self, *args):
        return self.run_watch(*args, cwd=self.repo, env=git_env())

    def test_carry_keeps_the_types_this_run_did_not_resolve(self):
        candidate = self.tree("candidate", {"linux": LOCK.replace("2.1.318", "2.1.321")})
        result = self.run_git_watch("carry", "nightly", str(candidate), "main", "lock/watch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2.1.320", (candidate / "images/cloud/gis/versions.lock").read_text())
        self.assertIn("2.1.321", (candidate / "images/cloud/linux/versions.lock").read_text(),
                      "a type this run resolved keeps this run's candidate")
        self.assertFalse((candidate / "images/cloud/linux-gpu").exists(), "the branch didn't change linux-gpu")

    def test_carry_ignores_what_main_changed_since(self):
        self.write("linux-gpu", LOCK.replace("2.1.318", "2.1.330"))
        self.git("commit", "-qam", "main moves on")
        candidate = self.tree("candidate", {})
        self.run_git_watch("carry", "nightly", str(candidate), "main", "lock/watch")
        self.assertFalse((candidate / "images/cloud/linux-gpu").exists())

    def test_proposed_when_the_branch_is_main_plus_the_candidate(self):
        branch = {"linux": LOCK.replace("2.1.318", "2.1.319"), "gis": LOCK.replace("2.1.318", "2.1.320"), "linux-gpu": LOCK}
        self.assertEqual(self.run_git_watch("proposed", str(self.tree("same", branch)), "main", "lock/watch").returncode, 0)
        other = dict(branch, linux=LOCK.replace("2.1.318", "2.1.322"))
        self.assertEqual(self.run_git_watch("proposed", str(self.tree("other", other)), "main", "lock/watch").returncode, 1)

    def test_not_proposed_once_main_moved(self):
        branch = {"linux": LOCK.replace("2.1.318", "2.1.319"), "gis": LOCK.replace("2.1.318", "2.1.320"), "linux-gpu": LOCK}
        (self.repo / "README").write_text("x")
        self.git("add", "README")
        self.git("commit", "-qm", "main moves on")
        self.assertEqual(self.run_git_watch("proposed", str(self.tree("same", branch)), "main", "lock/watch").returncode, 1)


class FakeGitHubBase(Base):
    """main with one lock, a candidate that moves Claude, and the fake gh."""
    MAIN = "a" * 40

    def setUp(self):
        super().setUp()
        executable(self.bin / "gh", FAKE_GH)
        self.state_path = self.tmp / "state.json"
        self.state = {"refs": {"main": self.MAIN}, "pulls": [], "main_history": [self.MAIN],
                      "commits": {self.MAIN: {"parent": None, "files": {"images/cloud/linux/versions.lock": LOCK}}}}
        self.save()
        self.candidate = self.tree("candidate", {"linux": LOCK.replace("2.1.318", "2.1.320")})
        (self.tmp / "body.md").write_text("what moved\n")

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def load(self):
        self.state = json.loads(self.state_path.read_text())
        return self.state

    def propose(self, title="lock(linux): claude 2.1.320"):
        return self.run_watch("propose", str(self.candidate), title, str(self.tmp / "body.md"),
                              env={"FAKE_GH_STATE": str(self.state_path), "FAKE_REPO": "lolay/agent-images"})


class ProposeTests(FakeGitHubBase):
    def test_opens_a_pull_request_with_auto_merge(self):
        result = self.propose()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load()
        head = state["refs"]["lock/watch"]
        commit = state["commits"][head]
        self.assertEqual(commit["parent"], self.MAIN)
        self.assertIn("claude - 2.1.320", commit["files"]["images/cloud/linux/versions.lock"])
        self.assertEqual(commit["message"], {"headline": "lock(linux): claude 2.1.320", "body": "what moved\n"})
        self.assertNotIn("lock/watch-staging", state["refs"])
        [pr] = state["pulls"]
        self.assertEqual((pr["state"], pr["head"], pr["base"], pr["title"], pr["body"], pr.get("auto_merge")),
                         ("open", "lock/watch", "main", "lock(linux): claude 2.1.320", "what moved\n", "SQUASH"))
        self.assertIn(f"pull request #1 at {head}, auto-merge on", result.stdout)

    def test_refreshes_the_open_pull_request(self):
        self.assertEqual(self.propose().returncode, 0)
        # main moves on, and the next run proposes on its head.
        state = self.load()
        state["refs"]["main"] = "b" * 40
        state["main_history"].append("b" * 40)
        state["commits"]["b" * 40] = {"parent": self.MAIN, "files": state["commits"][self.MAIN]["files"]}
        self.save()
        result = self.propose("lock(linux): claude 2.1.321")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load()
        self.assertEqual(len(state["pulls"]), 1, "the same pull request, not a second one")
        [pr] = state["pulls"]
        self.assertEqual((pr["state"], pr["title"]), ("open", "lock(linux): claude 2.1.321"),
                         "the branch never pointed at main, so the pull request wasn't marked merged")
        self.assertEqual(state["commits"][state["refs"]["lock/watch"]]["parent"], "b" * 40)

    def test_a_leftover_staging_branch_is_replaced(self):
        self.state["refs"]["lock/watch-staging"] = self.MAIN
        self.save()
        self.assertEqual(self.propose().returncode, 0, self.propose().stderr)

    def test_guard_runs_before_any_call(self):
        bad = self.candidate / ".github/workflows/x.yml"
        bad.parent.mkdir(parents=True)
        bad.write_text("on: push\n")
        sneaky = self.candidate / "images/cloud/linux/Dockerfile"
        sneaky.write_text("FROM scratch\n")
        result = self.propose()
        state = self.load()
        self.assertEqual(state.get("log", []), [], "nothing reached GitHub")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to push", result.stderr)

    def refused_without_a_call(self, result):
        self.assertEqual(self.load().get("log", []), [], "nothing reached GitHub")
        self.assertEqual(result.returncode, 1)
        self.assertIn("link or device", result.stderr)

    def test_a_lock_that_links_out_of_the_tree_is_refused(self):
        # The resolve job runs upstream code; a link named like a lock would
        # commit what it points at, here a token.
        secret = self.tmp / "secret"
        secret.write_text("GH_TOKEN=ghs_leak\n")
        lock = self.candidate / "images/cloud/linux/versions.lock"
        lock.unlink()
        lock.symlink_to(secret)
        self.refused_without_a_call(self.propose())

    def test_a_lock_that_is_a_fifo_is_refused(self):
        lock = self.candidate / "images/cloud/linux/versions.lock"
        lock.unlink()
        os.mkfifo(lock)
        self.refused_without_a_call(self.propose())

    def test_close(self):
        self.assertEqual(self.propose().returncode, 0)
        result = self.run_watch("close", env={"FAKE_GH_STATE": str(self.state_path), "FAKE_REPO": "lolay/agent-images"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.load()["pulls"][0]["state"], "closed")
        again = self.run_watch("close", env={"FAKE_GH_STATE": str(self.state_path), "FAKE_REPO": "lolay/agent-images"})
        self.assertEqual((again.returncode, again.stdout), (0, ""))


class RetryTests(FakeGitHubBase):
    """The open lock pull request's failed build is rerun when there's nothing
    new to push, up to the script's retry_max attempts."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.propose().returncode, 0)
        self.head = self.load()["refs"]["lock/watch"]

    def build(self, status="completed", conclusion="failure", attempt=1, head=None, event="pull_request"):
        state = self.load()
        state.setdefault("runs", []).append({
            "id": 100 + len(state["runs"]), "workflow": "cloud-build.yml", "event": event,
            "head_sha": head or self.head, "status": status, "conclusion": conclusion, "run_attempt": attempt})
        self.save()

    def retry(self):
        result = self.run_watch("retry", env={"FAKE_GH_STATE": str(self.state_path), "FAKE_REPO": "lolay/agent-images"})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout, [line for line in self.load().get("log", []) if line.startswith("rerun")]

    def test_a_failed_build_is_rerun(self):
        self.build()
        stdout, reruns = self.retry()
        self.assertEqual(reruns, ["rerun 100"])
        self.assertIn("attempt 2 of 4", stdout)

    def test_a_timed_out_build_is_rerun(self):
        self.build(conclusion="timed_out")
        self.assertEqual(self.retry()[1], ["rerun 100"])

    def test_the_newest_run_for_the_head_is_the_one_read(self):
        self.build(conclusion="failure")
        self.build(conclusion="success")
        stdout, reruns = self.retry()
        self.assertEqual(reruns, [])
        self.assertIn("run 101: success", stdout)

    def test_a_run_for_an_older_head_is_ignored(self):
        self.build(head="c" * 40)
        stdout, reruns = self.retry()
        self.assertEqual(reruns, [])
        self.assertIn("No cloud-build run", stdout)

    def test_a_running_build_is_left_alone(self):
        self.build(status="in_progress", conclusion=None)
        self.assertEqual(self.retry()[1], [])

    def test_a_green_build_is_left_alone(self):
        self.build(conclusion="success")
        self.assertEqual(self.retry()[1], [])

    def test_a_cancelled_build_is_left_alone(self):
        self.build(conclusion="cancelled")
        self.assertEqual(self.retry()[1], [])

    def test_stops_at_the_cap(self):
        self.build(attempt=4)
        stdout, reruns = self.retry()
        self.assertEqual(reruns, [])
        self.assertIn("left red for a person", stdout)

    def test_below_the_cap_still_reruns(self):
        self.build(attempt=3)
        self.assertEqual(self.retry()[1], ["rerun 100"])

    def test_no_open_pull_request(self):
        self.build()
        state = self.load()
        state["pulls"][0]["state"] = "closed"
        self.save()
        stdout, reruns = self.retry()
        self.assertEqual(reruns, [])
        self.assertIn("No lock pull request is open", stdout)


class TokenTests(Base):
    """token: a JWT signed with the App's key, then an installation token
    narrowed to this repository."""

    FAKE_CURL = r'''#!/usr/bin/env python3
import base64, json, os, subprocess, sys, tempfile
args = sys.argv[1:]
def log(line):
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(line + "\n")
expect = ["-fsS", "-X"]
if args[:2] != expect:
    sys.exit("curl: fake: unexpected options " + " ".join(args))
method, rest = args[2], args[3:]
headers, body, url = [], None, None
i = 0
while i < len(rest):
    if rest[i] == "-H":
        value = rest[i + 1]
        if value.startswith("@"):
            value = open(value[1:]).read().strip()
        headers.append(value); i += 2
    elif rest[i] == "--data-binary":
        body = rest[i + 1]; i += 2
    elif rest[i].startswith("-"):
        sys.exit("curl: option " + rest[i] + ": is unknown")
    else:
        url = rest[i]; i += 1
auth = next(h for h in headers if h.startswith("Authorization: Bearer "))[len("Authorization: Bearer "):]
api = os.environ["GITHUB_API_URL"]
if method == "DELETE" and url == api + "/installation/token":
    log("revoke " + auth); sys.exit(0)
header, payload, signature = auth.split(".")
pad = lambda s: s + "=" * (-len(s) % 4)
claims = json.loads(base64.urlsafe_b64decode(pad(payload)))
with tempfile.NamedTemporaryFile() as data, tempfile.NamedTemporaryFile() as sig:
    data.write((header + "." + payload).encode()); data.flush()
    sig.write(base64.urlsafe_b64decode(pad(signature))); sig.flush()
    ok = subprocess.run(["openssl", "dgst", "-sha256", "-verify", os.environ["FAKE_PUBLIC_KEY"], "-signature", sig.name, data.name],
                        capture_output=True).returncode == 0
if not ok or claims["iss"] != os.environ["FAKE_APP_ID"] or claims["exp"] - claims["iat"] > 600:
    sys.stderr.write("curl: (22) The requested URL returned error: 401\n"); sys.exit(22)
if method == "GET" and url == api + "/repos/lolay/agent-images/installation":
    print(json.dumps({"id": 42})); sys.exit(0)
if method == "POST" and url == api + "/app/installations/42/access_tokens":
    log("token " + body)
    print(json.dumps({"token": "ghs_fake", "expires_at": "2026-11-07T00:00:00Z"})); sys.exit(0)
sys.stderr.write("curl: (22) The requested URL returned error: 404\n"); sys.exit(22)
'''

    def setUp(self):
        super().setUp()
        executable(self.bin / "curl", self.FAKE_CURL)
        key, public = self.tmp / "key.pem", self.tmp / "public.pem"
        subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(public)], check=True, capture_output=True)
        self.log = self.tmp / "log"
        self.env = {"LOCK_BOT_APP_ID": "123", "LOCK_BOT_PRIVATE_KEY": key.read_text(), "FAKE_APP_ID": "123",
                    "FAKE_PUBLIC_KEY": str(public), "FAKE_LOG": str(self.log), "GITHUB_API_URL": "https://api.example"}

    def test_a_narrowed_token(self):
        result = self.run_watch("token", env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ghs_fake\n")
        request = json.loads(self.log.read_text().split(" ", 1)[1])
        self.assertEqual(request, {"repositories": ["agent-images"],
                                   "permissions": {"contents": "write", "pull_requests": "write"}})

    def test_a_wrong_key_gets_nothing(self):
        other = self.tmp / "other.pem"
        subprocess.run(["openssl", "genrsa", "-out", str(other), "2048"], check=True, capture_output=True)
        result = self.run_watch("token", env={**self.env, "LOCK_BOT_PRIVATE_KEY": other.read_text()})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_revoke(self):
        result = self.run_watch("revoke", env={**self.env, "GH_TOKEN": "ghs_fake"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(), "revoke ghs_fake\n")


class WorkflowTests(unittest.TestCase):
    """The settings the watch's safety rests on, read as text (actionlint, in
    make cloud-lint, checks the workflows' syntax)."""

    def test_gate_reports_on_every_pull_request(self):
        build = (REPO / ".github/workflows/cloud-build.yml").read_text()
        trigger = build.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(trigger.strip(), "pull_request:",
                         "a paths filter would leave a pull request outside it waiting for gate forever")
        gate = build.split("\n  gate:\n", 1)[1]
        self.assertIn("needs: [changes, check, build]", gate)
        self.assertIn("if: always()", gate)

    def test_only_the_propose_job_holds_the_key(self):
        watch = (REPO / ".github/workflows/lock-watch.yml").read_text()
        resolve, propose = watch.split("\n  propose:\n", 1)
        self.assertNotIn("secrets.", resolve)
        self.assertNotIn("environment:", resolve)
        self.assertIn("environment: lock-bot", propose)
        self.assertIn("if: github.ref == 'refs/heads/main'", resolve)

    def test_the_retry_job_holds_no_key_and_runs_only_on_retry(self):
        watch = (REPO / ".github/workflows/lock-watch.yml").read_text()
        resolve = watch.split("\n  propose:\n", 1)[0]
        retry = watch.split("\n  retry:\n", 1)[1]
        self.assertNotIn("secrets.", retry)
        self.assertNotIn("environment:", retry)
        self.assertIn("if: needs.resolve.outputs.action == 'retry'", retry)
        self.assertIn("actions: write", retry)
        self.assertNotIn("actions: write", resolve, "resolve runs what upstream hands it")
        self.assertIn("action=retry", resolve)

    def test_the_weekly_schedule_is_the_one_the_class_step_reads(self):
        watch = (REPO / ".github/workflows/lock-watch.yml").read_text()
        crons = [line.split('"')[1] for line in watch.splitlines() if line.strip().startswith("- cron:")]
        self.assertEqual(crons, ["17 8-22 * * *", "47 4 * * 0"])
        self.assertIn(f'WEEKLY: "{crons[1]}"', watch)
        self.assertEqual(watch.count("timezone: America/Los_Angeles"), 2)


if __name__ == "__main__":
    unittest.main()
