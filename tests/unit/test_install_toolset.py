"""images/toolsets/install-toolset.sh's record: the one link from a toolset's
files to what agent-toolset-checks and write-claude-md.sh read (r2.f1)."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable

SCRIPT = REPO / "images/toolsets/install-toolset.sh"


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        # install-toolset.sh finds toolsets beside itself, so it runs from a copy
        # among fixture toolsets with no packages.txt, Brewfile or install.sh:
        # only the record runs.
        self.toolsets = self.tmp / "toolsets"
        self.toolsets.mkdir()
        shutil.copy(SCRIPT, self.toolsets / "install-toolset.sh")
        alpha = self.toolsets / "alpha"
        alpha.mkdir()
        for name, text in {
            "check.txt": "alpha: alpha --version\n",
            "smoke.sh": "#!/bin/bash\necho alpha: ok\n",
            "CLAUDE.md": "- alpha\n",
            "profile.sh": "export ALPHA=1\n",
            "path.txt": "/opt/alpha/bin\n",
        }.items():
            (alpha / name).write_text(text)
        (self.toolsets / "beta").mkdir()
        (self.toolsets / "beta" / "CLAUDE.md").write_text("- beta\n")
        self.record = self.tmp / "record"
        self.fake_bin = self.tmp / "bin"
        # The record is written with sudo; the fake runs the command as is and,
        # like sudo, refuses options it isn't given here.
        write_executable(
            self.fake_bin / "sudo",
            '[[ "$1" != -* ]] || { echo "sudo: fake: unexpected option $1" >&2; exit 1; }\nexec "$@"\n',
        )

    def install(self, *names):
        return subprocess.run(
            ["bash", str(self.toolsets / "install-toolset.sh"), *names],
            env=base_env(self.tmp, self.fake_bin, AGENT_TOOLSETS_DIR=str(self.record)),
            capture_output=True, text=True, timeout=30,
        )

    def test_records_each_file_under_its_suffix(self):
        result = self.install("alpha", "beta")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(path.name for path in self.record.iterdir()),
            ["alpha.check", "alpha.md", "alpha.path", "alpha.profile.sh", "alpha.smoke.sh",
             "beta.md", "toolsets.txt"],
        )
        self.assertEqual((self.record / "alpha.smoke.sh").read_text(), "#!/bin/bash\necho alpha: ok\n")
        self.assertEqual((self.record / "alpha.check").read_text(), "alpha: alpha --version\n")

    def test_names_each_toolset_once_in_install_order(self):
        self.assertEqual(self.install("beta", "alpha").returncode, 0)
        self.assertEqual(self.install("alpha").returncode, 0)
        self.assertEqual((self.record / "toolsets.txt").read_text(), "beta\nalpha\n")

    def test_an_unknown_toolset_fails(self):
        result = self.install("gamma")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no toolset gamma", result.stderr)


if __name__ == "__main__":
    unittest.main()
