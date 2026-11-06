"""images/toolsets/install-toolset.sh: what `record` leaves in the record directory."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable

SCRIPT = REPO / "images/toolsets/install-toolset.sh"

FILES = {
    "CLAUDE.md": "md",
    "check.txt": "check",
    "smoke.sh": "smoke.sh",
    "profile.sh": "profile.sh",
    "path.txt": "path",
}


class InstallToolsetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # The script finds its toolsets next to itself, so run a copy beside fake ones.
        self.toolsets = self.tmp / "toolsets"
        self.toolsets.mkdir()
        self.script = self.toolsets / "install-toolset.sh"
        shutil.copy(SCRIPT, self.script)
        self.record_dir = self.tmp / "record"
        self.bin = self.tmp / "bin"
        # No root here: sudo runs its command as the current user, and refuses
        # what real sudo would (no command).
        write_executable(
            self.bin / "sudo",
            '(($# > 0)) || { echo "usage: sudo command" >&2; exit 1; }\nexec "$@"\n',
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def toolset(self, name, *files):
        directory = self.toolsets / name
        directory.mkdir()
        for file in files:
            (directory / file).write_text(f"{name} {file}\n")

    def install(self, *names):
        return subprocess.run(
            ["bash", str(self.script), *names],
            env=base_env(self.tmp, self.bin, AGENT_TOOLSETS_DIR=str(self.record_dir)),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_record_installs_each_file_under_the_toolsets_name(self):
        self.toolset("go", *FILES)
        result = self.install("go")
        self.assertEqual(result.returncode, 0, result.stderr)
        for file, suffix in FILES.items():
            self.assertEqual((self.record_dir / f"go.{suffix}").read_text(), f"go {file}\n", file)

    def test_record_lists_a_toolset_once_in_install_order(self):
        self.toolset("go", "check.txt")
        self.toolset("node", "smoke.sh")
        self.assertEqual(self.install("go", "node").returncode, 0)
        self.assertEqual(self.install("go").returncode, 0)
        self.assertEqual((self.record_dir / "toolsets.txt").read_text(), "go\nnode\n")

    def test_record_skips_the_files_a_toolset_lacks(self):
        self.toolset("go", "check.txt")
        self.assertEqual(self.install("go").returncode, 0)
        self.assertEqual(sorted(p.name for p in self.record_dir.iterdir()), ["go.check", "toolsets.txt"])

    def test_an_unknown_toolset_is_refused(self):
        result = self.install("nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no toolset nope", result.stderr)


if __name__ == "__main__":
    unittest.main()
