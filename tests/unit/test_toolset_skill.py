"""images/toolsets/skills/agent-toolset-checks: the session skill and the runner agree."""

import re
import unittest

from support import REPO

SKILL = REPO / "images/toolsets/skills/agent-toolset-checks/SKILL.md"
RUNNER = REPO / "images/toolsets/toolset-checks.sh"


def runner_subcommands():
    """The subcommands toolset-checks.sh's dispatch accepts."""
    case = RUNNER.read_text().rsplit('case "${1:-}" in', 1)[1]
    return set(re.findall(r"^([a-z][a-z-]*)\)", case, re.MULTILINE))


class SkillTests(unittest.TestCase):
    def test_frontmatter_names_the_skill(self):
        text = SKILL.read_text()
        match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(match, "no frontmatter")
        fields = dict(line.split(": ", 1) for line in match.group(1).splitlines())
        self.assertEqual(fields["name"], SKILL.parent.name)
        self.assertGreater(len(fields["description"]), 40)

    def test_it_names_only_subcommands_the_runner_has(self):
        named = set(re.findall(r"agent-toolset-checks ([a-z][a-z-]*)", SKILL.read_text()))
        self.assertTrue(named, "the skill names no subcommands")
        self.assertEqual(runner_subcommands(), {"versions", "smoke-list", "live", "smoke"})
        self.assertLessEqual(named, runner_subcommands())


if __name__ == "__main__":
    unittest.main()
