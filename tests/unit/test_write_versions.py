"""images/toolsets/write-versions.sh: an image's versions report, against fake tools."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, write_executable

SCRIPT = REPO / "images/toolsets/write-versions.sh"

# Each fake answers only the exact arguments the report passes it, and refuses
# anything else as the real tool would refuse a flag it doesn't know.
FAKES = {
    "claude": ('[[ "$*" == --version ]]', "echo '2.1.319 (Claude Code)'"),
    "agent-toolset-checks": ('[[ "$*" == versions ]]', "printf 'go: go1.27.3\\nnode: v26.13.0\\n'"),
    "brew": ('[[ "$*" == "list --versions" ]]', "printf 'go 1.27.3\\nnode 26.13.0\\nopenssl@3 3.6.0\\n'"),
    "rustup": ('[[ "$*" == "toolchain list" ]]', "echo 'stable-x86_64-unknown-linux-gnu (active, default)'"),
    "tenv": ('[[ "$*" == "tf list" ]]', "echo '* 1.17.1 (set by /home/ubuntu/.tenv/Terraform/version)'"),
    "pixi": ('[[ "$*" == "list --manifest-path $FAKE_MANIFEST" ]]', "echo 'gdal 3.13.3'"),
    "dpkg-query": ("[[ \"$*\" == '-W -f ${Package} ${Version}\\n' ]]", "printf 'bash 5.2.21\\ncurl 8.5.0\\n'"),
}


class WriteVersionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        # Only these from the host, so a real brew or rustup can't stand in.
        self.bin.mkdir()
        for tool in ("date", "head", "wc", "dirname"):
            os.symlink(shutil.which(tool), self.bin / tool)
        self.manifest = self.tmp / "gis/pixi.toml"
        self.manifest.parent.mkdir()
        self.manifest.write_text("[workspace]\n")
        for name, (accepts, answer) in FAKES.items():
            self.fake(name, accepts, answer)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fake(self, name, accepts, answer):
        write_executable(
            self.bin / name,
            f'{accepts} || {{ echo "{name}: unknown arguments: $*" >&2; exit 2; }}\n{answer}\n',
        )

    def report(self, *args):
        result = subprocess.run(
            [shutil.which("bash"), str(SCRIPT), *(args or ("linux", "abc123"))],
            env={"PATH": str(self.bin), "AGENT_GIS_MANIFEST": str(self.manifest),
                 "FAKE_MANIFEST": str(self.manifest)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result

    def test_every_section_when_every_tool_is_there(self):
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        out = result.stdout
        self.assertTrue(out.startswith("# agent-linux\n"))
        self.assertIn("- Commit: abc123\n", out)
        self.assertIn("- Claude Code: 2.1.319 (Claude Code)\n", out)
        for heading, line in (
            ("## Binary checks", "node: v26.13.0"),
            ("## Homebrew (with dependencies)", "openssl@3 3.6.0"),
            ("## Rust toolchains", "stable-x86_64-unknown-linux-gnu"),
            ("## Terraform (tenv)", "1.17.1"),
            (f"## pixi environment ({self.manifest.parent})", "gdal 3.13.3"),
            ("## apt", "curl 8.5.0"),
        ):
            self.assertIn(heading, out)
            self.assertIn(line, out)
        self.assertIn("<summary>2 packages</summary>", out)
        self.assertNotIn("exited", out)

    def test_a_section_whose_tool_is_missing_is_left_out(self):
        for name in ("rustup", "tenv", "pixi", "claude"):
            (self.bin / name).unlink()
        out = self.report().stdout
        self.assertNotIn("Rust toolchains", out)
        self.assertNotIn("Terraform", out)
        self.assertNotIn("pixi", out)
        self.assertNotIn("Claude Code", out)
        self.assertIn("## Homebrew (with dependencies)", out)

    def test_pixi_without_the_gis_manifest_is_left_out(self):
        self.manifest.unlink()
        self.assertNotIn("pixi environment", self.report().stdout)

    def test_a_failing_section_is_kept_and_says_so(self):
        # A binary check that needs the runtime fails at build time; the report
        # records it rather than failing the build.
        self.fake("agent-toolset-checks", '[[ "$*" == versions ]]',
                  "echo 'gpu: missing (no GPU)'; exit 1")
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gpu: missing (no GPU)", result.stdout)
        self.assertIn("agent-toolset-checks versions exited 1.", result.stdout)

    def test_refuses_without_type_and_commit(self):
        result = self.report("linux")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr)


if __name__ == "__main__":
    unittest.main()
