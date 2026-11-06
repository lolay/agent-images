"""scripts/cloud/record-check.sh: a built image's toolset record matches the
source, so an empty or broken record fails the pull request check (r2.f1)."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable

SCRIPT = REPO / "scripts/cloud/record-check.sh"
LISTING_SCRIPT = 'cd "${AGENT_TOOLSETS_DIR:?}" && cat toolsets.txt && echo --- && ls -1'


class RecordCheckTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        files = {
            "images/cloud/base/Dockerfile": "FROM ubuntu\nRUN bash /t/install-toolset.sh core\n",
            "images/cloud/linux/Dockerfile": (
                "FROM agent-base\nRUN bash /t/install-toolset.sh go\nRUN bash /t/install-toolset.sh browser\n"
            ),
            "images/toolsets/core/check.txt": "git: git --version\n",
            "images/toolsets/go/check.txt": "go: go version\n",
            "images/toolsets/go/smoke.sh": "#!/bin/bash\n",
            "images/toolsets/browser/packages.txt": "libnss3\n",
        }
        for name, text in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(text)
        self.fake_bin = self.repo / "bin"
        # docker run as record-check.sh calls it, and nothing else: an unknown
        # flag, image or script exits 125 the way docker refuses a bad call.
        write_executable(
            self.fake_bin / "docker",
            'expected=(run --rm --entrypoint /bin/bash agent-linux:local -c "$FAKE_SCRIPT")\n'
            'if [[ "$*" != "${expected[*]}" || $# != ${#expected[@]} ]]; then\n'
            '  echo "docker: fake: unexpected call: $*" >&2; exit 125\n'
            "fi\n"
            'printf "%s" "$FAKE_LISTING"\n',
        )

    def check(self, listing, type_="linux", image="agent-linux:local"):
        return subprocess.run(
            ["bash", str(SCRIPT), type_, image],
            cwd=self.repo,
            env=base_env(self.repo, self.fake_bin, FAKE_LISTING=listing, FAKE_SCRIPT=LISTING_SCRIPT),
            capture_output=True, text=True, timeout=30,
        )

    def test_a_complete_record_passes(self):
        result = self.check("core\ngo\nbrowser\n---\ncore.check\ngo.check\ngo.smoke.sh\ntoolsets.txt\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout.strip(), "record: pass linux (3 toolsets, 2 with binary checks, 1 with smoke tests)"
        )

    def test_a_smoke_test_recorded_under_the_wrong_name_fails(self):
        """r2's mutation: record() writing <name>.smoke instead of <name>.smoke.sh."""
        result = self.check("core\ngo\nbrowser\n---\ncore.check\ngo.check\ngo.smoke\ntoolsets.txt\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("record: FAIL linux (no go.smoke.sh)", result.stdout)

    def test_names_with_no_files_fail(self):
        result = self.check("core\ngo\nbrowser\n---\ntoolsets.txt\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no core.check", result.stdout)
        self.assertIn("no go.check", result.stdout)
        self.assertIn("no go.smoke.sh", result.stdout)

    def test_a_missing_or_reordered_toolset_fails(self):
        for recorded in ("core\ngo\n", "core\nbrowser\ngo\n", ""):
            with self.subTest(recorded=recorded):
                result = self.check(recorded + "---\ncore.check\ngo.check\ngo.smoke.sh\ntoolsets.txt\n")
                self.assertEqual(result.returncode, 1)
                self.assertIn("Dockerfiles install [core go browser]", result.stdout)

    def test_no_listing_fails(self):
        result = self.check("")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no record listing", result.stdout)

    def test_docker_failing_fails(self):
        result = self.check("core\n---\n", image="agent-missing:local")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("record: pass", result.stdout)

    def test_an_unknown_type_is_a_usage_error(self):
        result = self.check("", type_="nope")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
