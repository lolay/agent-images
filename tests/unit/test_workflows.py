"""Rules every workflow's `run:` steps follow, read as text (actionlint, in make
cloud-lint, checks their syntax, not these)."""

import re
import unittest

from support import REPO

WORKFLOWS = sorted((REPO / ".github/workflows").glob("*.yml"))


def run_steps(text):
    """Each `run:` step's script, with the line it starts on: a block scalar's
    lines, or a one-line value."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", lines[i])
        if not match:
            i += 1
            continue
        indent, value, start = len(match.group(1)), match.group(2), i + 1
        i += 1
        if value in ("|", "|-", ">", ">-"):
            body = []
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                body.append(lines[i])
                i += 1
            value = "\n".join(body)
        yield start, value


def code(script):
    """The script with comments dropped and quoted strings emptied, so a `|` in a
    jq filter or a sentence isn't read as a pipe."""
    out = []
    for line in script.splitlines():
        if line.strip().startswith("#"):
            continue
        line = re.sub(r"'[^']*'", "''", line)
        line = re.sub(r'"(?:[^"\\]|\\.)*"', '""', line)
        out.append(re.split(r"\s#", line, maxsplit=1)[0])
    return "\n".join(out)


def pipes(script):
    """Whether the script pipes one command into another (`|`, not `||`)."""
    return re.search(r"(?<!\|)\|(?![|&])", code(script)) is not None


def sets_pipefail(script):
    return re.search(r"^\s*set -[a-z]*o pipefail\b", script, re.M) is not None


class PipefailTests(unittest.TestCase):
    def test_every_piped_step_sets_pipefail(self):
        # r3.f5: a step's default shell is `bash -e {0}`, without pipefail, so a
        # piped step takes its last command's status: `lock-watch.sh close | tee`
        # reported success when the close failed.
        missing = []
        for workflow in WORKFLOWS:
            for line, script in run_steps(workflow.read_text()):
                if pipes(script) and not sets_pipefail(script):
                    missing.append(f"{workflow.relative_to(REPO)}:{line}")
        self.assertEqual(missing, [], "piped run steps without `set -euo pipefail`")

    def test_the_reader_finds_the_pipes_it_must(self):
        self.assertTrue(pipes("scripts/cloud/lock-watch.sh close | tee -a \"$GITHUB_STEP_SUMMARY\""))
        self.assertTrue(pipes("echo 'a' |\n  sudo tee /etc/x"))
        self.assertFalse(pipes("gh api x --jq '.[0] | .number'"))
        self.assertFalse(pipes("a || b\n# a | b"))
        self.assertTrue(sets_pipefail("set -euo pipefail\na | b"))
        self.assertFalse(sets_pipefail("set -eu\na | b"))
        steps = list(run_steps("      - name: x\n        run: |\n          a | b\n          c\n      - name: y\n        run: d\n"))
        self.assertEqual(steps, [(2, "          a | b\n          c"), (6, "d")])


if __name__ == "__main__":
    unittest.main()
