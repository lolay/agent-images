"""images/toolsets/write-claude-md.sh fills a CLAUDE.md template with the toolsets installed."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO

SCRIPT = REPO / "images/toolsets/write-claude-md.sh"

TEMPLATE = "# This machine\n\n## Tools\n\n<!-- toolsets -->\n- No Docker.\n\n## Storage\n"


class WriteClaudeMdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.records = self.tmp / "toolsets"
        self.records.mkdir()
        self.template = self.tmp / "template.md"
        self.template.write_text(TEMPLATE)
        self.output = self.tmp / "CLAUDE.md"

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def record(self, *toolsets):
        names = []
        for name, fragment in toolsets:
            names.append(name)
            if fragment is not None:
                (self.records / f"{name}.md").write_text(fragment)
        (self.records / "toolsets.txt").write_text("".join(f"{name}\n" for name in names))

    def write(self):
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.template), str(self.output), str(self.records)],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_fragments_replace_the_marker_in_install_order(self):
        self.record(("core", "- gh and jq.\n"), ("node", "- Node with pnpm,\n  and corepack gone.\n"))
        result = self.write()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.output.read_text(),
            "# This machine\n\n## Tools\n\n- gh and jq.\n- Node with pnpm,\n  and corepack gone.\n"
            "- No Docker.\n\n## Storage\n",
        )

    def test_a_toolset_without_a_claude_md_adds_nothing(self):
        self.record(("core", "- gh.\n"), ("android", None))
        self.assertEqual(self.write().returncode, 0)
        self.assertIn("- gh.\n- No Docker.\n", self.output.read_text())

    def test_a_template_without_the_marker_fails_and_writes_nothing(self):
        self.template.write_text("# This machine\n")
        self.record(("core", "- gh.\n"))
        result = self.write()
        self.assertEqual(result.returncode, 1)
        self.assertIn("<!-- toolsets -->", result.stderr)
        self.assertFalse(self.output.exists())

    def test_no_recorded_toolsets_fails(self):
        result = self.write()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.output.exists())

    def test_every_session_template_has_the_marker(self):
        for template in ("images/cloud/base/claude/CLAUDE.md", "images/linux/files/claude/CLAUDE.md"):
            self.assertIn("\n<!-- toolsets -->\n", (REPO / template).read_text(), template)

    def test_every_toolset_fragment_is_bullets(self):
        for fragment in sorted((REPO / "images/toolsets").glob("*/CLAUDE.md")):
            lines = fragment.read_text().splitlines()
            self.assertTrue(lines and lines[0].startswith("- "), fragment)
            for line in lines[1:]:
                self.assertTrue(line.startswith(("- ", "  ")), f"{fragment}: {line!r}")


if __name__ == "__main__":
    unittest.main()
