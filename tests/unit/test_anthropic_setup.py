"""scripts/anthropic/setup.sh installs agent-linux's toolsets, read from linux's
Dockerfile build chain as the image builds them, not a copy of the list (r1.f34),
and the session gate lints it.

`setup.sh toolsets` prints the list and exits before anything that needs root,
so the tests run it as is, from one directory above the checkout as
Anthropic's environment does."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO

SETUP = Path("scripts/anthropic/setup.sh")


def chain_toolsets(root, kind="linux"):
    """The toolsets kind's image installs, root parent's first, parsed here
    independently of scripts/cloud/lib.sh."""
    chain = []
    while kind and kind not in chain:
        chain.append(kind)
        text = (root / "images/cloud" / kind / "Dockerfile").read_text()
        parent = re.search(r"(?m)^FROM\s+agent-([a-z0-9-]+)", text)
        kind = parent and parent.group(1).replace("base-gpu", "base")
    toolsets = []
    for kind in reversed(chain):
        text = (root / "images/cloud" / kind / "Dockerfile").read_text()
        toolsets += re.findall(r"(?m)install-toolset\.sh\s+([a-z0-9-]+)\s*$", text)
    return toolsets


def run_setup(root, *args):
    # One directory above the checkout, as Anthropic runs it.
    return subprocess.run(["bash", str(root / SETUP), *args], cwd=root.parent, capture_output=True, text=True)


class SetupToolsetsTests(unittest.TestCase):

    def test_it_installs_linuxs_chain(self):
        result = run_setup(REPO, "toolsets")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = chain_toolsets(REPO)
        self.assertEqual(result.stdout.split(), expected)
        # base's core is in it: the chain, not linux's Dockerfile alone.
        self.assertEqual(expected[0], "core")
        self.assertGreater(len(expected), 5, expected)

    def test_it_follows_a_toolset_added_to_linux_or_base(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        root = tmp / "agent-images"
        for path in ("scripts/anthropic", "scripts/cloud", "hooks/gcp"):
            shutil.copytree(REPO / path, root / path)
        for dockerfile in REPO.glob("images/cloud/*/Dockerfile"):
            target = root / dockerfile.relative_to(REPO)
            target.parent.mkdir(parents=True)
            shutil.copy(dockerfile, target)
        for kind, toolset in (("linux", "added-to-linux"), ("base", "added-to-base")):
            with (root / "images/cloud" / kind / "Dockerfile").open("a") as f:
                f.write(f"RUN bash /tmp/toolsets/install-toolset.sh {toolset}\n")
        result = run_setup(root, "toolsets")
        self.assertEqual(result.returncode, 0, result.stderr)
        listed = result.stdout.split()
        self.assertEqual(listed, chain_toolsets(root))
        self.assertLess(listed.index("added-to-base"), listed.index("native"))
        self.assertEqual(listed[-1], "added-to-linux")

    def test_an_unknown_argument_fails_before_any_setup(self):
        result = run_setup(REPO, "toolset")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: setup.sh [toolsets]", result.stderr)


class LintTests(unittest.TestCase):

    def test_the_session_gate_lints_it(self):
        result = subprocess.run(["make", "--no-print-directory", "-n", "cloud-lint"], cwd=REPO, capture_output=True,
                                text=True, check=True)
        for tool in ("shellcheck", "shfmt"):
            with self.subTest(tool=tool):
                line = re.search(rf"(?m)^{tool} .*$", result.stdout).group(0)
                self.assertIn(f" {SETUP}", line)


if __name__ == "__main__":
    unittest.main()
