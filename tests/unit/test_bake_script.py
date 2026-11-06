"""scripts/cloud/bake.sh: a runner type builds from its lock, with its versions
report; the orchestrator builds at CLAUDE_VERSION. docker is a fake that records
what bake was given and refuses anything but `buildx bake -f <file>`."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, write_executable

SCRIPT = REPO / "scripts/cloud/bake.sh"
LOCK = (
    "# header\n"
    "apt cmake 3.28.3-1build7\n"
    "apt-snapshot - 20261101T000000Z\n"
    "base - mirror.gcr.io/library/ubuntu:24.04@sha256:aaa\n"
    "brew go 1.27.3\n"
    "claude - 2.1.319\n"
    "rust - 1.99.1\n"
)


class BakeScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.locks = self.tmp / "locks"
        (self.locks / "linux").mkdir(parents=True)
        (self.locks / "linux" / "versions.lock").write_text(LOCK)
        self.calls = self.tmp / "calls"
        write_executable(
            self.tmp / "bin" / "docker",
            'if [[ "$1 $2 $3 $4" != "buildx bake -f images/cloud/docker-bake.hcl" ]]; then\n'
            '  echo "docker: fake: unexpected call: $*" >&2; exit 125\n'
            "fi\n"
            "python3 -c 'import json, os, sys; print(json.dumps({\"argv\": sys.argv[1:], "
            "\"claude\": os.environ.get(\"CLAUDE_VERSION\"), \"commit\": os.environ.get(\"COMMIT\")}))' "
            '"${@:5}" >>"$FAKE_CALLS"\n',
        )

    def bake(self, *args, **env):
        settings = {
            **os.environ,
            "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}",
            "LOCK_DIR": str(self.locks),
            "FAKE_CALLS": str(self.calls),
            "COMMIT": "abc123",
        }
        settings.pop("CLAUDE_VERSION", None)
        settings.update(env)
        return subprocess.run(
            ["bash", str(SCRIPT), *args], cwd=REPO, env=settings, capture_output=True, text=True, timeout=30
        )

    def call(self):
        lines = self.calls.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0])

    def test_a_runner_type_builds_from_its_lock_with_its_report(self):
        result = self.bake("linux", "--load")
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.call()
        self.assertEqual(call["argv"][-3:], ["--load", "linux", "versions-linux"])
        sets = [call["argv"][i + 1] for i, arg in enumerate(call["argv"]) if arg == "--set"]
        self.assertEqual(sorted(sets), [
            "*.args.APT_SNAPSHOT=20261101T000000Z",
            "*.args.BASE_IMAGE=mirror.gcr.io/library/ubuntu:24.04@sha256:aaa",
            "*.args.CLAUDE_VERSION=2.1.319",
            "*.args.RUST_TOOLCHAIN=1.99.1",
        ])
        self.assertEqual(call["commit"], "abc123")

    def test_the_lock_wins_over_a_claude_version_in_the_environment(self):
        self.bake("linux", CLAUDE_VERSION="latest")
        self.assertIn("*.args.CLAUDE_VERSION=2.1.319", self.call()["argv"])

    def test_a_type_without_a_lock_fails_before_building(self):
        result = self.bake("gis")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no lock for gis", result.stderr)
        self.assertFalse(self.calls.exists(), "built without a lock")

    def test_the_orchestrator_takes_claude_version(self):
        result = self.bake("orchestrator", "--load", CLAUDE_VERSION="2.1.300")
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.call()
        self.assertEqual(call["argv"], ["--load", "orchestrator"])
        self.assertEqual(call["claude"], "2.1.300")
        self.assertEqual(self.bake("orchestrator").returncode, 0)
        self.assertEqual(json.loads(self.calls.read_text().splitlines()[1])["claude"], "latest")

    def test_usage(self):
        self.assertEqual(self.bake().returncode, 2)



class BuildsFromTheLockTests(unittest.TestCase):
    """Every build of a runner type reads its lock: the deploy (push and
    scheduled, one job) through lock.sh bake-args, the pull request check and
    make through bake.sh. A path back to `latest` would build what no lock
    records."""

    def section(self, text, start, end):
        return text[text.index(start):text.index(end)]

    def test_the_deploy_feeds_bake_from_the_lock(self):
        workflow = (REPO / ".github/workflows/cloud-deploy.yml").read_text()
        types = self.section(workflow, "\n  types:\n", "\n  vm-images:\n")
        self.assertIn('scripts/cloud/lock.sh bake-args "$TYPE"', types)
        self.assertIn("set: ${{ steps.cache.outputs.set }}", types)
        self.assertNotIn("CLAUDE_VERSION", types, "the lock sets a runner type's Claude Code")

    def test_the_pull_request_check_builds_through_make(self):
        workflow = (REPO / ".github/workflows/cloud-build.yml").read_text()
        self.assertIn('make cloud-build TYPE="$TARGET"', workflow)
        self.assertNotIn("docker buildx bake", workflow)
        self.assertNotIn("claude-code-releases", workflow)

    def test_make_builds_through_bake_sh(self):
        makefile = (REPO / "Makefile").read_text()
        for target in ("cloud-build:", "cloud-ci:"):
            recipe = makefile[makefile.index("\n" + target):].split("\n\n", 1)[0]
            self.assertIn("scripts/cloud/bake.sh", recipe, target)
        self.assertNotIn("docker buildx bake", makefile)


if __name__ == "__main__":
    unittest.main()
