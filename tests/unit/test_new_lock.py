"""scripts/cloud/new-lock.sh: cloud-build resolves a new runner type's first lock,
hands it over as an artifact and fails until it's committed; a type with a lock
is never resolved (r4.f11).

lock.sh is a fake beside a copy of the script: `path` runs the real lock.sh, so
the lock's location and LOCK_DIR stay lock.sh's, and `resolve` records the call
and refuses what the real one refuses (a type with no env file in infra/gcp, any
other subcommand), as AGENTS.md "Test fakes" asks."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO

FAKE_LOCK_SH = r'''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
if args[:1] == ["path"]:
    os.execv("/bin/bash", ["bash", os.path.join(here, "real-lock.sh")] + args)
if len(args) == 2 and args[0] == "resolve":
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write("resolve " + args[1] + "\n")
    if not os.path.exists(os.path.join("infra/gcp", args[1] + ".env")):
        sys.exit("lock: " + args[1] + " isn't a Cloud Run type in infra/gcp")
    if os.environ.get("FAKE_RESOLVE_FAIL"):
        sys.exit("lock: docker buildx bake --print " + args[1] + " failed")
    sys.stdout.write(os.environ["FAKE_LOCK"])
    sys.exit(0)
sys.exit("lock: usage: lock.sh resolve <type> | bake-args <type> | diff <old> <new> | summary <old> <new> | pins <type> | path <type>")
'''

RESOLVED = (
    "# header\n"
    "apt-snapshot - 20261115T000000Z\n"
    "base - mirror.gcr.io/library/ubuntu:24.04@sha256:bbb\n"
    "claude - 2.1.320\n"
)


class NewLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        # The script and lib.sh (which real-lock.sh sources) under their repo
        # paths, so `dirname`/../../hooks resolves as in the repo.
        scripts = self.tmp / "repo/scripts/cloud"
        shutil.copytree(REPO / "scripts/cloud", scripts)
        shutil.copytree(REPO / "hooks/gcp", self.tmp / "repo/hooks/gcp")
        (scripts / "lock.sh").rename(scripts / "real-lock.sh")
        fake = scripts / "lock.sh"
        fake.write_text(FAKE_LOCK_SH)
        fake.chmod(0o755)
        self.script = scripts / "new-lock.sh"
        self.locks = self.tmp / "locks"
        self.locks.mkdir()
        self.log = self.tmp / "log"
        self.output = self.tmp / "output"
        self.summary = self.tmp / "summary"
        self.out = self.tmp / "out/linux"

    def run_script(self, *args, lock_dir=True, cwd=REPO, **env):
        settings = {
            **os.environ,
            "FAKE_LOG": str(self.log),
            "FAKE_LOCK": RESOLVED,
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "GITHUB_RUN_ID": "4242",
            "GITHUB_REPOSITORY": "lolay/agent-images",
        }
        settings.pop("LOCK_DIR", None)
        if lock_dir:
            settings["LOCK_DIR"] = str(self.locks)
        settings.update(env)
        settings = {k: v for k, v in settings.items() if v is not None}
        return subprocess.run(
            ["bash", str(self.script), *args], cwd=cwd, env=settings, capture_output=True, text=True, timeout=30
        )

    def resolved(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_a_type_with_a_lock_is_never_resolved(self):
        lock = self.locks / "linux/versions.lock"
        lock.parent.mkdir()
        lock.write_text("# by the watch\nclaude - 2.1.300\n")
        result = self.run_script("linux", str(self.out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("linux has a lock", result.stdout)
        self.assertEqual(self.resolved(), [], "resolved a type that has a lock")
        self.assertEqual(lock.read_text(), "# by the watch\nclaude - 2.1.300\n")
        self.assertFalse(self.out.exists())
        self.assertFalse(self.output.exists(), "named an artifact for a type that has a lock")

    def test_every_type_with_a_lock_is_left_alone(self):
        # Every Cloud Run type, as cloud-build's matrix lists them (make
        # cloud-targets, from changed-targets.sh --all), each given a lock here:
        # a new type's pull request has none committed yet, and its pre-push
        # gate must still pass so it can get one from cloud-build.
        listed = subprocess.run(
            ["bash", "scripts/cloud/changed-targets.sh", "--all"], cwd=REPO, capture_output=True, text=True, check=True
        )
        types = [entry["type"] for entry in json.loads(listed.stdout)["types"]]
        self.assertIn("linux", types)
        for type_ in types:
            with self.subTest(type_):
                lock = self.locks / type_ / "versions.lock"
                lock.parent.mkdir()
                lock.write_text(f"# {type_}\n")
                result = self.run_script(type_, str(self.tmp / "out" / type_))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"{type_} has a lock ({lock})", result.stdout)
                self.assertEqual(lock.read_text(), f"# {type_}\n")
        self.assertEqual(self.resolved(), [])
        self.assertFalse(self.output.exists())

    def test_a_type_without_a_lock_is_resolved_handed_over_and_fails(self):
        result = self.run_script("linux", str(self.out))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.resolved(), ["resolve linux"])
        self.assertEqual((self.out / "versions.lock").read_text(), RESOLVED)
        self.assertEqual(self.output.read_text(), "artifact=versions-lock-linux\n")
        download = f"gh run download 4242 --repo lolay/agent-images --name versions-lock-linux --dir {self.locks}/linux"
        summary = self.summary.read_text()
        self.assertIn(download, summary)
        self.assertIn(f"git add {self.locks}/linux/versions.lock", summary)
        self.assertIn(RESOLVED, summary)
        self.assertIn("::error title=linux has no lock::Commit the lock", result.stdout)
        self.assertIn(download, result.stdout)
        self.assertFalse((self.locks / "linux").exists(), "wrote the lock into the tree")

    def test_the_default_download_lands_at_the_types_lock(self):
        # With lock.sh's own default, the fetched file lands where bake reads it.
        repo = self.tmp / "repo"
        (repo / "infra/gcp").mkdir(parents=True)
        (repo / "infra/gcp/newtype.env").write_text("BACKEND=run\n")
        result = self.run_script("newtype", str(self.out), lock_dir=False, cwd=repo)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.resolved(), ["resolve newtype"])
        self.assertIn("--name versions-lock-newtype --dir images/cloud/newtype\n", self.summary.read_text())

    def test_a_failed_resolve_names_no_artifact(self):
        result = self.run_script("linux", str(self.out), FAKE_RESOLVE_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("couldn't resolve a lock for linux", result.stderr)
        self.assertFalse(self.output.exists(), "named an artifact for a failed resolve")
        self.assertFalse(self.summary.exists())

    def test_it_needs_githubs_step_variables_before_resolving(self):
        for name in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "GITHUB_RUN_ID", "GITHUB_REPOSITORY"):
            with self.subTest(name):
                result = self.run_script("linux", str(self.out), **{name: None})
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"{name} isn't set", result.stderr)
        self.assertEqual(self.resolved(), [])

    def test_usage(self):
        self.assertEqual(self.run_script("linux").returncode, 1)
        self.assertEqual(self.run_script("../linux", str(self.out)).returncode, 1)
        self.assertEqual(self.resolved(), [])


class CloudBuildWiringTests(unittest.TestCase):
    """cloud-build.yml runs new-lock.sh on every runner type's leg before the
    build, and uploads what it resolved only when it failed for a missing lock."""

    def setUp(self):
        workflow = (REPO / ".github/workflows/cloud-build.yml").read_text()
        self.build = workflow[workflow.index("\n  build:\n"):workflow.index("\n  gate:\n")]

    def step(self, start):
        text = self.build[self.build.index(start):]
        return text[:text.find("\n      - ", 1)]

    def test_every_runner_type_checks_its_lock_before_building(self):
        lock = self.step("      - name: Lock ${{ matrix.target }}")
        self.assertIn("id: lock", lock)
        self.assertIn("if: matrix.target != 'orchestrator'", lock)
        self.assertIn('scripts/cloud/new-lock.sh "$TARGET" "out/new-lock/$TARGET"', lock)
        self.assertLess(self.build.index("scripts/cloud/new-lock.sh"), self.build.index("make cloud-build"))

    def test_the_resolved_lock_is_uploaded_only_after_a_missing_lock(self):
        upload = self.step("      - uses: actions/upload-artifact@", )
        self.assertIn("if: ${{ failure() && steps.lock.outputs.artifact != '' }}", upload)
        self.assertIn("name: ${{ steps.lock.outputs.artifact }}", upload)
        self.assertIn("path: out/new-lock/${{ matrix.target }}/versions.lock", upload)
        self.assertLess(self.build.index("new-lock.sh"), self.build.index("steps.lock.outputs.artifact"))
        self.assertLess(self.build.index("steps.lock.outputs.artifact"), self.build.index("make cloud-build"))


if __name__ == "__main__":
    unittest.main()
