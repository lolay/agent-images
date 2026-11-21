"""CI's tool versions have one source, scripts/ci/tools.env (r1.f16, f17): every
workflow that installs one of the tools reads it, Renovate bumps every line of it,
and no workflow or doc restates a version. The workflows are read as text
(actionlint, in make cloud-lint, checks their syntax)."""

import json
import re
import subprocess
import unittest

from support import REPO, read_env_file

TOOLS_ENV = REPO / "scripts/ci/tools.env"
WORKFLOWS = REPO / ".github/workflows"
# The step that loads tools.env into a job's environment.
LOAD = """run: grep -E '^[A-Z_]+=' scripts/ci/tools.env >>"$GITHUB_ENV\""""
# Each tool's name as prose and config spell it, for the copy check below.
TOOL_NAMES = ("packer",)
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
                    op, version = re.fullmatch(r"\s*(>=)\s*([\d.]+)\s*", part).groups()
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
        for name, version in versions().items():
            for path in sorted(WORKFLOWS.glob("*.yml")):
                with self.subTest(tool=name, workflow=path.name):
                    self.assertNotIn(version, path.read_text())


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
                copies.append(f"{name}:{text.count(chr(10), 0, match.start()) + 1}: {match.group(0)!r}")
        self.assertEqual(copies, [], "say where the version is set (scripts/ci/tools.env), not the version")

    def test_the_copy_check_sees_each_spelling(self):
        for text in ("| Packer | 1.16.1 |", "installs Packer 1.16.1", "`packer` ≥ 1.16.1", "Packer v1.16",
                     '- tool: packer\n  version: ">=1.16.1"', "packer@1.16.2"):
            with self.subTest(text=text):
                self.assertTrue(COPY.search(text))
        for text in ("hashicorp/setup-packer@ce93c3c # v3.4.0", "| packer-plugin-tart | 1.21.0 |",
                     "packer init images/macos", "- tool: packer\n  hint: brew install packer"):
            with self.subTest(text=text):
                self.assertFalse(COPY.search(text))


if __name__ == "__main__":
    unittest.main()
