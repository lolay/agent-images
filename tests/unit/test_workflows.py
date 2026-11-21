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


def _scan(line, i, close):
    """The code in line from i up to an unquoted `close` (or the line's end, or
    a comment), and where it stopped: single-quoted strings emptied, and of a
    double-quoted string only its $(...) substitutions kept, scanned the same
    way, so `"$(git log | head -n1)"` keeps its pipe and `'a | b'` has none."""
    out = []
    while i < len(line):
        c = line[i]
        if close and c == close:
            return "".join(out), i
        if c == "\\":
            out.append(line[i:i + 2])
            i += 2
        elif c == "'":
            end = line.find("'", i + 1)
            out.append("''")
            i = len(line) if end < 0 else end + 1
        elif c == '"':
            out.append('"')
            i += 1
            while i < len(line) and line[i] != '"':
                if line[i] == "\\":
                    i += 2
                elif line.startswith("$(", i):
                    inner, i = _scan(line, i + 2, ")")
                    out.append(" $(" + inner + ") ")
                    i += 1
                else:
                    i += 1
            out.append('"')
            i += 1
        elif line.startswith("$(", i) or c == "(":
            opener = "$(" if c == "$" else "("
            inner, i = _scan(line, i + len(opener), ")")
            out.append(opener + inner + ")")
            i += 1
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            break
        else:
            out.append(c)
            i += 1
    return "".join(out), i


def code(script):
    """The script with comments dropped and quoted strings emptied but for the
    command substitutions in double quotes, so a `|` in a jq filter or a
    sentence isn't read as a pipe, and one in `x="$(a | b)"` is."""
    return "\n".join(_scan(line, 0, None)[0] for line in script.splitlines() if not line.strip().startswith("#"))


def pipes(script):
    """Whether the script pipes one command into another (`|` or `|&`, not `||`)."""
    return re.search(r"(?<!\|)\|(?!\|)", code(script)) is not None


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
        # A pipe in a command substitution, quoted or not, and bash's |& (r3.f5
        # review): each hides a failure as `a | b` does.
        for script in ('x="$(git log | head -n1)"', "x=$(git log | head -n1)", 'echo "v: $(a "$b" | c)"',
                       "make lint |& tee log", 'echo "$(a "$(b | c)")"'):
            with self.subTest(script):
                self.assertTrue(pipes(script))
        for script in ('x="$(gh api x --jq \'.[0] | .number\')"', 'echo "a | b $(c || d)"', "a || b && c", "(a || b)"):
            with self.subTest(script):
                self.assertFalse(pipes(script))
        self.assertTrue(sets_pipefail("set -euo pipefail\na | b"))
        self.assertFalse(sets_pipefail("set -eu\na | b"))
        steps = list(run_steps("      - name: x\n        run: |\n          a | b\n          c\n      - name: y\n        run: d\n"))
        self.assertEqual(steps, [(2, "          a | b\n          c"), (6, "d")])


if __name__ == "__main__":
    unittest.main()
