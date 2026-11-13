"""The session gate (make cloud-lint cloud-test) and make doctor's session profile
leave Packer out, so doctor passes on the linux image; cloud-lint-android and the
session-android profile carry it. A doctor that fails every session gets ignored
(AGENTS.md, "make doctor must pass")."""

import re
import subprocess
import unittest

from support import REPO


def recipe(target):
    """The commands make would run for target, without running them."""
    result = subprocess.run(
        ["make", "-n", "--no-print-directory", target],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def profile_tools(name):
    """The tool: items of a top-level profile in triage.yaml, read as text: the
    tests don't depend on PyYAML."""
    text = (REPO / "triage.yaml").read_text()
    match = re.search(rf"^{re.escape(name)}:\n(.*?)(?=^\S|\Z)", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"no profile {name} in triage.yaml")
    return re.findall(r"^\s+- tool: (\S+)", match.group(1), re.M)


class SessionGateTests(unittest.TestCase):
    def test_cloud_lint_runs_no_packer(self):
        self.assertNotRegex(recipe("cloud-lint"), r"(?m)^\s*packer ")

    def test_cloud_lint_android_validates_the_template(self):
        commands = recipe("cloud-lint-android")
        for step in ("packer init", "packer fmt -check", "packer validate"):
            self.assertIn(step, commands)
        self.assertIn("images/android/gce", commands)

    def test_cloud_ci_runs_cloud_lint_android(self):
        self.assertRegex(recipe("cloud-ci"), r"(?m)^\s*packer validate ")

    def test_session_profile_checks_no_packer(self):
        tools = profile_tools("session")
        self.assertIn("python3", tools)
        self.assertNotIn("packer", tools)

    def test_session_android_profile_adds_packer(self):
        self.assertIn("packer", profile_tools("session-android"))


if __name__ == "__main__":
    unittest.main()
