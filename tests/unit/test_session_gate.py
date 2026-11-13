"""In a cloud session, make pre-commit (cloud-lint cloud-test) and make doctor's
session profile leave Packer out, so doctor passes on the linux image; MODE=android
adds it to both, through cloud-lint and the session-android profile. A
doctor that fails every session gets ignored (AGENTS.md, "make doctor must pass")."""

import os
import re
import subprocess
import unittest

from support import REPO


def make_dry_run(*args, agent_type=None):
    """make -n: the commands make would run, without running them. agent_type set
    is a cloud session; None a host."""
    env = {k: v for k, v in os.environ.items() if k not in ("AGENT_TYPE", "MODE")}
    if agent_type is not None:
        env["AGENT_TYPE"] = agent_type
    return subprocess.run(
        ["make", "-n", "--no-print-directory", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def recipe(*args, agent_type=None):
    result = make_dry_run(*args, agent_type=agent_type)
    if result.returncode != 0:
        raise AssertionError(f"make -n {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def profile_tools(name):
    """The tool: items of a top-level profile in triage.yaml, read as text: the
    tests don't depend on PyYAML."""
    text = (REPO / "triage.yaml").read_text()
    match = re.search(rf"^{re.escape(name)}:\n(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError(f"no profile {name} in triage.yaml")
    return re.findall(r"^\s+- tool: (\S+)", match.group(1), re.MULTILINE)


class SessionGateTests(unittest.TestCase):
    def test_cloud_lint_runs_no_packer(self):
        self.assertNotRegex(recipe("cloud-lint"), r"(?m)^\s*packer ")

    def test_cloud_lint_android_validates_the_template(self):
        commands = recipe("cloud-lint", "MODE=android")
        for step in ("packer init", "packer fmt -check", "packer validate"):
            self.assertRegex(commands, rf"(?m)^{step} .*images/android/gce")

    def test_cloud_lint_refuses_an_unknown_mode(self):
        result = subprocess.run(
            ["make", "--no-print-directory", "cloud-lint", "MODE=androd"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MODE must be run or android", result.stderr)

    def test_cloud_ci_always_checks_the_template(self):
        self.assertRegex(recipe("cloud-ci", "MODE=run"), r"(?m)^packer validate ")

    def test_session_pre_commit_is_the_cloud_gate_without_packer(self):
        commands = recipe("pre-commit", agent_type="linux")
        self.assertRegex(commands, r"(?m)^shellcheck ")
        self.assertRegex(commands, r"(?m)^python3 -m unittest ")
        self.assertNotRegex(commands, r"(?m)^\s*packer ")

    def test_session_pre_commit_android_adds_the_template(self):
        commands = recipe("pre-commit", "MODE=android", agent_type="linux")
        self.assertRegex(commands, r"(?m)^packer fmt -check -recursive images/android/gce$")
        self.assertRegex(commands, r"(?m)^packer validate ")
        self.assertRegex(commands, r"(?m)^python3 -m unittest ")

    def test_host_pre_commit_stays_the_macos_gate(self):
        commands = recipe("pre-commit")
        self.assertRegex(commands, r"(?m)^packer validate images/macos")
        self.assertNotIn("python3 -m unittest", commands)

    def test_session_profile_checks_no_packer(self):
        tools = profile_tools("session")
        self.assertIn("python3", tools)
        self.assertNotIn("packer", tools)

    def test_session_android_profile_adds_packer(self):
        self.assertIn("packer", profile_tools("session-android"))


if __name__ == "__main__":
    unittest.main()
