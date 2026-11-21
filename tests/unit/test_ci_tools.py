"""CI's tool versions have one source, scripts/ci/tools.env (r1.f16, f17): every
workflow that installs one of the tools reads it, Renovate bumps every line of it,
and no workflow or doc restates a version. The workflows are read as text
(actionlint, in make cloud-lint, checks their syntax)."""

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, read_env_file, write_executable

TOOLS_ENV = REPO / "scripts/ci/tools.env"
INSTALL = REPO / "scripts/ci/install-lint-tools.sh"
WORKFLOWS = REPO / ".github/workflows"
# The step that loads tools.env into a job's environment.
LOAD = """run: scripts/cloud/github-env.sh scripts/ci/tools.env >>"$GITHUB_ENV\""""
# The step that installs CI's shellcheck and shfmt and puts them first on PATH.
INSTALL_LINT = """scripts/ci/install-lint-tools.sh "$RUNNER_TEMP/bin"
          echo "$RUNNER_TEMP/bin" >>"$GITHUB_PATH\""""
# A make target that runs shellcheck and shfmt (ci runs lint; cloud-ci,
# cloud-lint).
LINTS = re.compile(r"\bmake\b[^\n]*\b(ci|lint|cloud-lint|cloud-ci|pre-commit)\b")
# Installing a CI tool any other way (a distribution's, Homebrew's) brings a
# second version.
OTHER_INSTALL = re.compile(r"\b(apt-get|apt|brew|pip3?|snap|npm|go)\b[^\n]*\binstall\b[^\n]*"
                           r"\b(shellcheck|shfmt|packer)\b")
# Each tool's name as prose and config spell it, for the copy check below.
TOOL_NAMES = ("packer", "shellcheck", "shfmt")
# A tool's name, then a version within a little punctuation of it ("Packer
# 1.16.1", "| Packer | 1.16.1", "`packer` ≥ 1.16.1", "Packer v1.16"), or on the
# next line's `version:` (triage.yaml's `- tool: packer` items). Not a name inside
# another one: setup-packer's own version, packer-plugin-tart's.
COPY = re.compile(r"(?<![\w-])(" + "|".join(TOOL_NAMES) + r")(?![\w-])[^\w\n]*"
                  r"(?:\n[^\S\n]*version:)?[^\w\n]*v?\d+\.\d+", re.IGNORECASE)
# Where a version may stand next to a tool's name: the source itself, what an
# image was built from (its lock), and the dated record of past reviews and
# handoffs.
NOT_COPIES = re.compile(r"^(scripts/ci/tools\.env|images/cloud/[^/]+/versions\.lock|specs/(reviews|handoffs)/.*"
                        r"|tests/unit/test_ci_tools\.py)$")
# And README's record of what the Linux images held at a dated lookup, which
# the toolsets set (newest stable at build time), not CI.
NOT_COPY_LINES = re.compile(r"^\| Linux toolchains \|")


def macos_packer_floor():
    """The Packer floor images/macos/plugins.pkr.hcl's required_version sets."""
    text = (REPO / "images/macos/plugins.pkr.hcl").read_text()
    return re.search(r'required_version\s*=\s*">=\s*([\d.]+)"', text).group(1)


def jobs(text):
    """A workflow's jobs, name to body, split at two-space indentation."""
    section = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([a-z][\w-]*):\n", section)
    return dict(zip(parts[1::2], parts[2::2]))


def steps(job):
    """A job's steps, in order, each as its text."""
    if "\n    steps:\n" not in "\n" + job:
        return []
    return re.split(r"(?m)^      - ", ("\n" + job).split("\n    steps:\n", 1)[1])[1:]


def workflow_jobs():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, body in jobs(path.read_text()).items():
            yield f"{path.name}:{name}", steps(body)


def versions():
    return read_env_file(TOOLS_ENV)


class SourceTests(unittest.TestCase):

    def test_every_version_is_renovate_managed(self):
        config = json.loads((REPO / ".github/renovate.json").read_text())
        self.assertIn("custom.regex", config["enabledManagers"])
        managers = [m for m in config.get("customManagers", [])
                    if any(re.search(p.strip("/"), "scripts/ci/tools.env") for p in m["managerFilePatterns"])]
        self.assertEqual(len(managers), 1, "one custom manager reads scripts/ci/tools.env")
        manager = managers[0]
        text = TOOLS_ENV.read_text()
        found = {}
        for pattern in manager["matchStrings"]:
            # JavaScript's named groups, in Python's spelling.
            for match in re.finditer(pattern.replace("(?<", "(?P<"), text):
                found[match.group("depName")] = match.group("currentValue")
                self.assertEqual(match.group("datasource"), "github-releases")
        # Every KEY=version line is matched: one without its comment would never
        # be bumped.
        self.assertEqual(sorted(found.values()), sorted(versions().values()))
        self.assertGreaterEqual(len(found), len(TOOL_NAMES))
        # Their tags are v<version>; the manager strips the v.
        self.assertEqual(re.fullmatch(manager["extractVersionTemplate"].replace("(?<", "(?P<"), "v1.2.3")
                         .group("version"), "1.2.3")
        rules = [r for r in config["packageRules"] if "custom.regex" in r.get("matchManagers", [])]
        self.assertTrue(rules, "no package rule matches the custom manager's updates")

    def test_ci_packer_meets_every_template_floor(self):
        packer = tuple(int(part) for part in versions()["PACKER_VERSION"].split("."))
        templates = sorted(REPO.glob("images/**/*.pkr.hcl"))
        floors = []
        for path in templates:
            for constraint in re.findall(r'required_version\s*=\s*"([^"]+)"', path.read_text()):
                for part in constraint.split(","):
                    match = re.fullmatch(r"\s*(>=)\s*([\d.]+)\s*", part)
                    self.assertIsNotNone(match, f"{path.relative_to(REPO)}: can't check {part.strip()!r} "
                                                "(only >= floors); teach this test the operator")
                    op, version = match.groups()
                    floors.append(path)
                    self.assertGreaterEqual(packer, tuple(int(n) for n in version.split(".")),
                                            f"{path.relative_to(REPO)} needs Packer {constraint}")
        self.assertGreaterEqual(len(set(floors)), 2, templates)


class WorkflowTests(unittest.TestCase):

    def test_every_packer_install_reads_the_one_source(self):
        installs = 0
        for job, job_steps in workflow_jobs():
            loaded = False
            for step in job_steps:
                if LOAD in step:
                    loaded = True
                if "hashicorp/setup-packer@" in step:
                    installs += 1
                    self.assertTrue(loaded, f"{job} installs Packer before loading scripts/ci/tools.env")
                    self.assertRegex(step, r"\n\s+version: \$\{\{ env\.PACKER_VERSION \}\}\n", job)
        # ci.yml, cloud-build.yml and cloud-deploy.yml: the check sees them.
        self.assertGreaterEqual(installs, 3)

    def test_no_workflow_restates_a_version(self):
        # As a `version:` value (setup-packer's input, anything like it), not
        # any substring: an action's tag comment (# v3.14.10) isn't a copy.
        for name, version in versions().items():
            literal = re.compile(r"(?m)^\s*version:\s*[\"']?v?" + re.escape(version) + r"[\"']?\s*$")
            for path in sorted(WORKFLOWS.glob("*.yml")):
                with self.subTest(tool=name, workflow=path.name):
                    self.assertIsNone(literal.search(path.read_text()))


class CopyTests(unittest.TestCase):

    def test_no_file_restates_a_tool_version(self):
        tracked = subprocess.run(["git", "ls-files"], cwd=REPO, check=True, capture_output=True,
                                 text=True).stdout.split()
        self.assertGreater(len(tracked), 100, tracked)
        copies = []
        for name in tracked:
            if NOT_COPIES.match(name):
                continue
            try:
                text = (REPO / name).read_text()
            except (UnicodeDecodeError, IsADirectoryError):
                continue
            for match in COPY.finditer(text):
                start = text.rfind("\n", 0, match.start()) + 1
                if name == "README.md" and NOT_COPY_LINES.match(text[start:]):
                    continue
                # The doctor's Packer floor is the macOS template's, checked
                # equal to it below, not a copy of CI's version.
                end = text.find("\n", match.end())
                if name == "triage.yaml" and text[match.start():end].endswith(f'">={macos_packer_floor()}"'):
                    continue
                copies.append(f"{name}:{text.count(chr(10), 0, match.start()) + 1}: {match.group(0)!r}")
        self.assertEqual(copies, [], "say where the version is set (scripts/ci/tools.env), not the version")

    def test_the_copy_check_sees_each_spelling(self):
        for text in ("| Packer | 1.16.1 |", "installs Packer 1.16.1", "`packer` ≥ 1.16.1", "Packer v1.16",
                     '- tool: packer\n  version: ">=1.16.1"', "packer@1.16.2",
                     "| shellcheck / shfmt | 0.11.0 / 3.14.1 |", "ShellCheck 0.10", "shfmt v3.14.1",
                     "- tool: shfmt\n  version: '>=3.12'"):
            with self.subTest(text=text):
                self.assertTrue(COPY.search(text))
        for text in ("hashicorp/setup-packer@ce93c3c # v3.4.0", "| packer-plugin-tart | 1.21.0 |",
                     "packer init images/macos", "- tool: packer\n  hint: brew install packer",
                     "shellcheck -x $(CLOUD_SCRIPTS)", "# shellcheck disable=SC2016"):
            with self.subTest(text=text):
                self.assertFalse(COPY.search(text))

    def test_the_doctor_asks_for_the_macos_template_floor(self):
        # make doctor's build profile catches an old Packer before make init
        # does, at the floor the template sets: one fact, read twice.
        triage = (REPO / "triage.yaml").read_text()
        found = re.search(r"- tool: packer\n(?:[^\S\n]+#[^\n]*\n)*[^\S\n]+version: \"(>=[\d.]+)\"", triage)
        self.assertIsNotNone(found, "triage.yaml's build profile has no Packer version check")
        self.assertEqual(found.group(1), ">=" + macos_packer_floor())

    def test_readme_exemption_is_only_the_image_record(self):
        # The one line exempted still names the tools, so the exemption isn't
        # hiding nothing, and README's other rows are checked.
        lines = [line for line in (REPO / "README.md").read_text().splitlines() if NOT_COPY_LINES.match(line)]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("(2026-", lines[0], "the image record is dated")


class LintToolWorkflowTests(unittest.TestCase):

    def test_every_lint_job_installs_the_pinned_tools_first(self):
        linting = 0
        for job, job_steps in workflow_jobs():
            installed = False
            for step in job_steps:
                if INSTALL_LINT in step:
                    installed = True
                runs = step.split("run:", 1)[1] if "run:" in step else ""
                if LINTS.search(runs):
                    linting += 1
                    self.assertTrue(installed, f"{job} lints before scripts/ci/install-lint-tools.sh")
        # ci.yml's make ci and cloud-build.yml's make cloud-lint.
        self.assertGreaterEqual(linting, 2)

    def test_no_workflow_installs_a_ci_tool_another_way(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            with self.subTest(workflow=path.name):
                self.assertEqual(OTHER_INSTALL.findall(path.read_text()), [])
        # The pattern sees the installs this replaced.
        self.assertTrue(OTHER_INSTALL.search("sudo apt-get install -y --no-install-recommends shellcheck shfmt"))
        self.assertTrue(OTHER_INSTALL.search("brew install shellcheck shfmt"))


# A fake curl that serves the release assets the real one would, at their
# exact URLs, and fails as `curl -f` does (22) on anything else. It takes only
# the flags the script passes.
FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, shutil, sys
args = sys.argv[1:]
out = None
url = None
while args:
    arg = args.pop(0)
    if arg == "-fsSL":
        continue
    if arg == "--retry":
        int(args.pop(0))
    elif arg == "-o":
        out = args.pop(0)
    elif arg.startswith("-"):
        sys.exit("curl: unknown option " + arg)
    elif url is None:
        url = arg
    else:
        sys.exit("curl: one URL at a time")
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(url + "\n")
assets = json.loads(os.environ["FAKE_ASSETS"])
if url not in assets or out is None:
    sys.exit(22)
shutil.copy(assets[url], out)
'''

FAKE_UNAME = r'''#!/bin/bash
case "$*" in
-s) printf '%s\n' "$FAKE_OS" ;;
-m) printf '%s\n' "$FAKE_ARCH" ;;
*) echo "uname: unexpected $*" >&2; exit 1 ;;
esac
'''


class InstallLintToolsTests(unittest.TestCase):
    """scripts/ci/install-lint-tools.sh: the release assets for the runner's
    platform, at tools.env's versions, checked by what they report."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.fake_bin = self.tmp / "fake-bin"
        write_executable(self.fake_bin / "curl", "").write_text(FAKE_CURL)
        write_executable(self.fake_bin / "uname", FAKE_UNAME.split("\n", 1)[1])
        self.log = self.tmp / "log"
        self.bin = self.tmp / "out/bin"
        self.versions = versions()

    def assets(self, platforms, shellcheck=None, shfmt=None):
        """Release assets at their real URLs for the given (shellcheck, shfmt)
        platform names, whose binaries report the given versions."""
        sc = self.versions["SHELLCHECK_VERSION"]
        sf = self.versions["SHFMT_VERSION"]
        assets = {}
        shellcheck_platform, shfmt_platform = platforms
        tarball = self.tmp / f"shellcheck.{shellcheck_platform}.tar.gz"
        body = ("#!/bin/sh\n[ \"$1\" = --version ] || exit 2\n"
                f"printf 'ShellCheck - shell script analysis tool\\nversion: %s\\nlicense: GPL\\n' "
                f"'{shellcheck or sc}'\n").encode()
        with tarfile.open(tarball, "w:gz") as tar:
            info = tarfile.TarInfo(f"shellcheck-v{sc}/shellcheck")
            info.size, info.mode = len(body), 0o755
            tar.addfile(info, io.BytesIO(body))
        assets[f"https://github.com/koalaman/shellcheck/releases/download/v{sc}/"
               f"shellcheck-v{sc}.{shellcheck_platform}.tar.gz"] = str(tarball)
        binary = self.tmp / f"shfmt_{shfmt_platform}"
        binary.write_text(f"#!/bin/sh\n[ \"$1\" = --version ] || exit 2\necho 'v{shfmt or sf}'\n")
        assets[f"https://github.com/mvdan/sh/releases/download/v{sf}/shfmt_v{sf}_{shfmt_platform}"] = str(binary)
        return assets

    def run_install(self, os_name, arch, assets):
        env = base_env(self.tmp, self.fake_bin, FAKE_OS=os_name, FAKE_ARCH=arch, FAKE_LOG=str(self.log),
                       FAKE_ASSETS=json.dumps(assets))
        return subprocess.run(["bash", str(INSTALL), str(self.bin)], env=env, capture_output=True, text=True)

    def installed_versions(self):
        sc = subprocess.run([str(self.bin / "shellcheck"), "--version"], capture_output=True, text=True, check=True)
        sf = subprocess.run([str(self.bin / "shfmt"), "--version"], capture_output=True, text=True, check=True)
        return sc.stdout, sf.stdout

    def test_each_runner_gets_its_platforms_assets(self):
        # ci.yml's macos-latest (Apple Silicon) and cloud-build.yml's ubuntu-24.04.
        for os_name, arch, platforms in (("Darwin", "arm64", ("darwin.aarch64", "darwin_arm64")),
                                         ("Linux", "x86_64", ("linux.x86_64", "linux_amd64"))):
            with self.subTest(os=os_name, arch=arch):
                shutil.rmtree(self.bin, ignore_errors=True)
                result = self.run_install(os_name, arch, self.assets(platforms))
                self.assertEqual(result.returncode, 0, result.stderr)
                shellcheck, shfmt = self.installed_versions()
                self.assertIn(f"version: {self.versions['SHELLCHECK_VERSION']}\n", shellcheck)
                self.assertEqual(shfmt, f"v{self.versions['SHFMT_VERSION']}\n")

    def test_an_asset_reporting_another_version_fails(self):
        for wrong in ({"shellcheck": "0.0.1"}, {"shfmt": "0.0.1"}):
            with self.subTest(wrong=wrong):
                assets = self.assets(("linux.x86_64", "linux_amd64"), **wrong)
                result = self.run_install("Linux", "x86_64", assets)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("not", result.stderr)

    def test_a_platform_with_no_release_fails(self):
        result = self.run_install("FreeBSD", "amd64", {})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no shellcheck or shfmt release for", result.stderr)
        self.assertFalse(self.log.exists(), "nothing downloaded")

    def test_the_session_gate_lints_it(self):
        # Both workflows run it; a session's gate is cloud-lint.
        result = subprocess.run(["make", "--no-print-directory", "-n", "cloud-lint"], cwd=REPO, capture_output=True,
                                text=True, check=True)
        for tool in ("shellcheck", "shfmt"):
            with self.subTest(tool=tool):
                line = re.search(rf"(?m)^{tool} .*$", result.stdout).group(0)
                self.assertIn(f" {INSTALL.relative_to(REPO)}", line)

    def test_a_missing_asset_fails(self):
        assets = self.assets(("linux.x86_64", "linux_amd64"))
        assets.pop(next(url for url in assets if "/mvdan/" in url))
        result = self.run_install("Linux", "x86_64", assets)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
