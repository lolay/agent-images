"""scripts/cloud/pr-check.sh: cloud-build's `changes` decision and `gate`
verdict, which main's ruleset and the lock bot's auto-merge rest on (r3.f15)."""

import ast
import itertools
import re
import subprocess
import unittest
from pathlib import Path

from support import REPO
from test_bake import run_types

SCRIPT = REPO / "scripts/cloud/pr-check.sh"
WORKFLOW = REPO / ".github/workflows/cloud-build.yml"
RESULTS = ("success", "failure", "cancelled", "skipped")


def run(*args, stdin=""):
    return subprocess.run(["bash", str(SCRIPT), *args], input=stdin, capture_output=True, text=True, timeout=30)


class RelevantTests(unittest.TestCase):
    def relevant(self, *paths):
        result = run("relevant", stdin="".join(path + "\n" for path in paths))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_a_lock_only_diff_is_checked(self):
        # The lock bot's pull requests: if this were "false", gate would pass
        # without building the new lock, and auto-merge would land it. Every
        # Cloud Run type (BACKEND=run in infra/gcp), as the deploy plans them.
        types = run_types()
        self.assertIn("linux", types)
        for type_ in types:
            with self.subTest(type_):
                self.assertEqual(self.relevant(f"images/cloud/{type_}/versions.lock"), "true\n")

    def test_a_specs_only_diff_is_not(self):
        self.assertEqual(self.relevant("specs/gcp.md", "specs/reviews/r3/f15.md"), "false\n")
        self.assertEqual(self.relevant("README.md", "Makefile.md", "AGENTS.md", "tests/sessions/linux.md"), "false\n")
        self.assertEqual(self.relevant(), "false\n", "an empty diff")

    def test_what_builds_smokes_or_tests_an_image_is_checked(self):
        for path in ("images/cloud/base/Dockerfile", "images/android/gce/build.pkr.hcl", "images/shared/agent-runner.sh",
                     "images/toolsets/core/packages.txt", "hooks/gcp/lib.sh", "infra/gcp/linux.env",
                     "scripts/cloud/lock.sh", "tests/unit/support.py", ".dockerignore", "Makefile", "triage.yaml",
                     ".github/workflows/cloud-build.yml", ".github/workflows/cloud-deploy.yml",
                     ".github/workflows/lock-watch.yml", "scripts/ci/tools.env", "scripts/anthropic/setup.sh",
                     ".github/renovate.json", "images/macos/build.pkr.hcl", "images/macos/scripts/install-packages.sh",
                     "scripts/lib.sh", "scripts/linux/host-setup.sh", "hooks/spawn-runner", "hooks/linux/spawn-runner"):
            with self.subTest(path):
                self.assertEqual(self.relevant("specs/gcp.md", path), "true\n")

    def test_every_workflow_is_checked(self):
        # The unit tests read every workflow (test_workflows.py's pipefail guard
        # among them), so a change to any one of them runs the check.
        workflows = sorted((REPO / ".github/workflows").glob("*.yml"))
        self.assertGreater(len(workflows), 3)
        for workflow in workflows:
            with self.subTest(workflow.name):
                self.assertEqual(self.relevant(str(workflow.relative_to(REPO))), "true\n")

    def test_lookalikes_are_not(self):
        for path in ("docs/images/cloud/x.png", "Makefile.md", "tests/sessions/x.md", "imagesx/cloud/x",
                     ".github/workflows/cloud-build.yml.orig", ".github/workflows/old/ci.yml",
                     ".github/renovate.json5", "scriptsx/x.sh", "hooksx/x", "infra/aws/x"):
            with self.subTest(path):
                self.assertEqual(self.relevant(path), "false\n")


# Docs: a test may read one (a table row, a heading), but a docs-only pull
# request doesn't run the check.
DOCS = re.compile(r"^specs/|^(?!images/).*\.md$")
# String literals in tests/unit that name a tracked path but aren't one: "vms"
# is a key of changed-targets.sh's JSON.
NOT_PATHS = {"vms"}


def tracked():
    return subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True,
                          check=True).stdout.splitlines()


def named_paths(files):
    """Each string literal in tests/unit that names a tracked file or directory,
    with the tracked files it covers. This file's own are fixtures and the
    exceptions above, so they're left out."""
    literals = set()
    for test in sorted((REPO / "tests/unit").glob("*.py")):
        if test.name == Path(__file__).name:
            continue
        for node in ast.walk(ast.parse(test.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value.rstrip("/"))
    named = {}
    for literal in literals:
        if literal in ("", ".") or any(c.isspace() for c in literal):
            continue
        covered = [f for f in files if f == literal or f.startswith(literal + "/")]
        if covered:
            named[literal] = covered
    return named


class EveryPathTheTestsReadTests(unittest.TestCase):
    """Every path a unit test names is one the check covers (the review of
    #155): a pull request changing only it would otherwise pass gate without
    the tests that guard it, as images/macos did. Derived from the tests' own
    string literals, so the next test that reads a new directory is held to it."""

    def test_every_named_path_is_checked(self):
        named = named_paths(tracked())
        self.assertIn("images/macos/build.pkr.hcl", named, "the reader finds REPO / \"...\" paths")
        self.assertIn("images/cloud", named, "and plain path strings")
        files = sorted({f for literal, covered in named.items() if literal not in NOT_PATHS
                        for f in covered if not DOCS.search(f)})
        result = subprocess.run(["bash", "-c", 'while IFS= read -r f; do printf "%s %s" "$f" '
                                 '"$(printf "%s\\n" "$f" | bash "$0" relevant)"; echo; done', str(SCRIPT)],
                                input="".join(f + "\n" for f in files), capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        verdicts = dict(line.rsplit(" ", 1) for line in result.stdout.splitlines())
        self.assertEqual(set(verdicts), set(files))
        self.assertEqual([f for f in files if verdicts[f] != "true"], [])

    def test_the_exceptions_are_still_needed(self):
        named = named_paths(tracked())
        for literal in NOT_PATHS:
            with self.subTest(literal):
                self.assertIn(literal, named, "a literal no test has any more: drop it from NOT_PATHS")
        docs = sorted({f for covered in named.values() for f in covered if DOCS.search(f)})
        self.assertTrue(docs, "the tests name docs (specs/gcp.md, README.md)")
        for doc in docs:
            with self.subTest(doc):
                result = run("relevant", stdin=doc + "\n")
                self.assertEqual(result.stdout, "false\n", "a docs-only change doesn't run the check")


class GateTests(unittest.TestCase):
    def gate(self, changes="success", relevant="true", check="success", build="success"):
        return run("gate", changes, relevant, check, build)

    def test_passes_when_check_and_build_pass(self):
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_passes_when_nothing_relevant_changed(self):
        # check and build were skipped: their `if` needs relevant.
        result = self.gate(relevant="false", check="skipped", build="skipped")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Nothing cloud-build checks changed.", result.stdout)

    def test_each_non_success_of_check_or_build_fails(self):
        for check, build in itertools.product(RESULTS, RESULTS):
            if (check, build) == ("success", "success"):
                continue
            with self.subTest(check=check, build=build):
                result = self.gate(check=check, build=build)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f"::error::check: {check}, build: {build}", result.stdout)

    def test_a_failed_changes_job_fails_whatever_else(self):
        for changes in ("failure", "cancelled", "skipped"):
            for relevant in ("true", "false", ""):
                with self.subTest(changes=changes, relevant=relevant):
                    result = self.gate(changes=changes, relevant=relevant)
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertIn(f"::error::changes: {changes}", result.stdout)

    def test_an_unreadable_relevant_fails(self):
        # A `changes` that succeeded without saying true or false is broken; the
        # workflow used to read anything but "true" as nothing to check.
        for relevant in ("", "yes", "True"):
            for check in ("success", "skipped"):
                with self.subTest(relevant=relevant, check=check):
                    result = self.gate(relevant=relevant, check=check, build=check)
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertIn(f"::error::changes gave relevant={relevant}", result.stdout)

    def test_usage(self):
        for args in ((), ("gate", "success"), ("relevant", "x"), ("nope",)):
            with self.subTest(args=args):
                self.assertEqual(run(*args).returncode, 2)


class WorkflowTests(unittest.TestCase):
    """cloud-build.yml hands both decisions to the script, with the results
    gate needs (test_lock_watch checks gate's trigger, needs and `if`)."""

    def test_changes_asks_the_script(self):
        changes = WORKFLOW.read_text().split("\n  changes:\n", 1)[1].split("\n  check:\n", 1)[0]
        self.assertIn('changed="$(git diff --name-only "$BASE...$HEAD")"', changes)
        self.assertIn('relevant="$(scripts/cloud/pr-check.sh relevant <<<"$changed")"', changes)
        self.assertIn('echo "relevant=$relevant" | tee -a "$GITHUB_OUTPUT"', changes)
        self.assertNotIn("grep", changes, "the paths are pr-check.sh's")

    def test_gate_asks_the_script_with_each_jobs_result(self):
        gate = WORKFLOW.read_text().split("\n  gate:\n", 1)[1]
        self.assertIn("uses: actions/checkout@", gate)
        for name, value in (("CHANGES", "needs.changes.result"), ("RELEVANT", "needs.changes.outputs.relevant"),
                            ("CHECK", "needs.check.result"), ("BUILD", "needs.build.result")):
            self.assertIn(f"{name}: ${{{{ {value} }}}}", gate)
        self.assertIn('run: scripts/cloud/pr-check.sh gate "$CHANGES" "$RELEVANT" "$CHECK" "$BUILD"', gate)


if __name__ == "__main__":
    unittest.main()
