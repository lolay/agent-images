"""In a cloud session, make pre-commit (cloud-lint cloud-test) and make doctor's
session profile leave Packer out, so doctor passes on the linux image; MODE=android
adds it to both, through cloud-lint and the session-android profile. A
doctor that fails every session gets ignored (AGENTS.md, "make doctor must pass")."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, write_executable


# What would leak the caller's settings into the make under test: AGENT_TYPE and
# MODE themselves, and the variables a parent make passes to its children, since
# the tests run inside make cloud-test, which CI calls with MODE=android.
CALLER_MAKE_ENV = ("AGENT_TYPE", "MODE", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES")


def clean_env():
    return {k: v for k, v in os.environ.items() if k not in CALLER_MAKE_ENV}


def make_dry_run(*args, agent_type=None):
    """make -n: the commands make would run, without running them. agent_type set
    is a cloud session; None a host."""
    env = clean_env()
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


# A fake triage that records the profile it was asked for. It rejects what the
# real one would: a flag it doesn't take, a missing value, a stray argument, and a
# profile triage.yaml doesn't define (a config error, exit 3, as --severity grades it).
FAKE_TRIAGE = r"""
log="${FAKE_TRIAGE_LOG:?}"
profile=default
while (($#)); do
	case "$1" in
	--profile) (($# >= 2)) || { echo "flag needs an argument: --profile" >&2; exit 2; }; profile="$2"; shift 2 ;;
	--profile=*) profile="${1#--profile=}"; shift ;;
	--var) (($# >= 2)) || { echo "flag needs an argument: --var" >&2; exit 2; }
		[[ "$2" == *=* ]] || { echo "--var wants name=value: $2" >&2; exit 2; }; shift 2 ;;
	-*) echo "unknown flag: $1" >&2; exit 2 ;;
	*) echo "fake triage reads ./triage.yaml only; got $1" >&2; exit 2 ;;
	esac
done
[[ -f triage.yaml ]] || { echo "no triage.yaml in $PWD" >&2; exit 3; }
grep -q "^$profile:" triage.yaml || { echo "unknown profile $profile" >&2; exit 3; }
printf '%s\n' "$profile" >>"$log"
"""


class DoctorTests(unittest.TestCase):
    """make doctor in a session maps MODE to a triage profile: run to session,
    android to session-android, anything else refused before triage runs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.log = self.tmp / "profiles"
        write_executable(self.tmp / "bin/triage", FAKE_TRIAGE)

    def doctor(self, *args):
        env = clean_env()
        env.update(AGENT_TYPE="linux", FAKE_TRIAGE_LOG=str(self.log),
                   PATH=f"{self.tmp / 'bin'}:{env['PATH']}")
        return subprocess.run(["make", "--no-print-directory", "doctor", *args], cwd=REPO, env=env,
                              capture_output=True, text=True, check=False)

    def profiles(self):
        return self.log.read_text().split() if self.log.exists() else []

    def test_the_fake_rejects_what_triage_would(self):
        triage = str(self.tmp / "bin/triage")
        env = {**os.environ, "FAKE_TRIAGE_LOG": str(self.log)}
        for args in (["--profle", "session"], ["--profile"], ["--profile", "nope"], ["other.yaml"]):
            with self.subTest(args=args):
                result = subprocess.run([triage, *args], cwd=REPO, env=env, capture_output=True, check=False)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.profiles(), [])

    def test_run_is_the_session_profile(self):
        for args in ((), ("MODE=run",)):
            with self.subTest(args=args):
                result = self.doctor(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.profiles(), ["session", "session"])

    def test_android_is_the_session_android_profile(self):
        result = self.doctor("MODE=android")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.profiles(), ["session-android"])

    def test_any_other_mode_is_refused_before_triage(self):
        for mode in ("build", "androd", ""):
            with self.subTest(mode=mode):
                result = self.doctor(f"MODE={mode}")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("MODE must be run or android in a session", result.stderr)
        self.assertEqual(self.profiles(), [])


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
            env=clean_env(),
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
