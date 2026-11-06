"""images/cloud/docker-bake.hcl builds every type infra/gcp declares.

The types are the BACKEND=run env files (scripts/cloud/changed-targets.sh --all);
the build graph is the bake file. Nothing else ties the two together, so a new
env file without its targets would pass CI and fail the deploy on main (r1.f2).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO

BAKE_FILE = "images/cloud/docker-bake.hcl"
CHANGED_TARGETS = REPO / "scripts/cloud/changed-targets.sh"

HAVE_BAKE = shutil.which("docker") is not None and subprocess.run(
    ["docker", "buildx", "version"], capture_output=True
).returncode == 0


def run_types():
    """The Cloud Run types, as the deploy and the pull request check plan them."""
    plan = subprocess.run(
        ["bash", str(CHANGED_TARGETS), "--all"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout
    return [entry["type"] for entry in json.loads(plan)["types"]]


def bake_print(*targets):
    result = subprocess.run(
        ["docker", "buildx", "bake", "-f", BAKE_FILE, "--print", *targets],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"bake --print {' '.join(targets)} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)["target"]


class BakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_BAKE:
            # The pull request check runs these: there, a missing buildx fails.
            if os.environ.get("GITHUB_ACTIONS") == "true":
                raise AssertionError("needs docker buildx, and this CI runner has none")
            raise unittest.SkipTest("needs docker buildx")

    def test_there_are_types(self):
        # An empty list would make the checks below pass vacuously.
        self.assertIn("linux", run_types())

    def test_every_type_has_its_targets(self):
        for type_ in run_types():
            with self.subTest(type=type_):
                targets = bake_print(type_)
                # The deploy pushes <type> and caches <type>-tools by those names
                # (cloud-deploy.yml); <type> is the runner stage on <type>-tools.
                self.assertIn(f"{type_}-tools", targets)
                image = targets[type_]
                self.assertEqual(image["dockerfile"], "images/cloud/runner/Dockerfile")
                self.assertEqual(image["contexts"]["agent-tools"], f"target:{type_}-tools")
                self.assertEqual(image["tags"], [f"agent-{type_}:local"])
                self.assertEqual(image["target"], "runner")
                self.assertEqual(image["args"]["AGENT_TYPE"], type_)

    def test_every_type_has_its_versions_report(self):
        for type_ in run_types():
            with self.subTest(type=type_):
                # The workflows read out/versions/<type>/versions.md after the bake.
                report = bake_print(f"versions-{type_}")[f"versions-{type_}"]
                self.assertEqual(report["target"], "versions")
                self.assertEqual(report["args"]["AGENT_TYPE"], type_)
                self.assertEqual(report["contexts"]["agent-tools"], f"target:{type_}-tools")
                self.assertEqual(report["output"], [{"type": "local", "dest": f"out/versions/{type_}"}])
                self.assertNotIn("tags", report)

    def test_push_and_load_keep_the_reports_local_output(self):
        # The deploy pushes and the pull request check loads in the same bake call
        # as the report; neither may turn the report into an image.
        for flag in ("--push", "--load"):
            with self.subTest(flag=flag):
                report = bake_print(flag, "linux", "versions-linux")["versions-linux"]
                self.assertEqual(report["output"], [{"type": "local", "dest": "out/versions/linux"}])

    def test_cloud_build_builds_each_runner_types_report_with_the_commit(self):
        """make cloud-build reproduces the pull request check's report (r2.f14)."""

        def targets(*args):
            out = subprocess.run(
                ["make", "-n", "cloud-build", *args], cwd=REPO, check=True, capture_output=True, text=True
            ).stdout
            line = next(l for l in out.splitlines() if "docker buildx bake" in l)
            return line, line.split("--load ")[1].split()

        line, built = targets("TYPE=linux", "COMMIT=abc123")
        self.assertEqual(built, ["linux", "versions-linux"])
        self.assertIn("COMMIT=abc123 ", line)
        self.assertEqual(targets("TYPE=orchestrator")[1], ["orchestrator"], "not a runner: no report")
        built = targets("TYPE=all")[1]
        for name in run_types():
            self.assertIn(name, built)
            self.assertIn(f"versions-{name}", built)
        self.assertIn("orchestrator", built)
        self.assertNotIn("versions-orchestrator", built)

    def test_the_orchestrator_has_its_target(self):
        self.assertEqual(bake_print("orchestrator")["orchestrator"]["tags"], ["agent-orchestrator:local"])

    def test_changed_targets_follows_the_bake_graph(self):
        """A change to any Dockerfile directory in a type's bake graph rebuilds that type.

        changed-targets reads the graph from the Dockerfiles' FROM lines, not the
        bake file, so this checks the two agree (r1.f32: a type FROM agent-base-gpu
        never reached base).
        """
        needed = {}  # directory under images/cloud -> the types whose graph uses it
        for type_ in run_types():
            for target in bake_print(type_).values():
                directory = Path(target["dockerfile"]).parent.name
                needed.setdefault(directory, set()).add(type_)

        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)

        def git(*args):
            return subprocess.run(
                ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
            ).stdout.strip()

        git("init", "-q", "-b", "main")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "test")
        for name in ("images/cloud", "images/toolsets", "infra/gcp"):
            shutil.copytree(REPO / name, repo / name)
        git("add", "-A")
        git("commit", "-q", "-m", "tree")

        for directory, types in sorted(needed.items()):
            with self.subTest(directory=directory):
                base = git("rev-parse", "HEAD")
                dockerfile = repo / "images/cloud" / directory / "Dockerfile"
                dockerfile.write_text(dockerfile.read_text() + "# changed\n")
                git("commit", "-q", "-am", f"change {directory}")
                plan = subprocess.run(
                    ["bash", str(CHANGED_TARGETS), base, git("rev-parse", "HEAD")],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                built = {entry["type"] for entry in json.loads(plan)["types"] if entry["build"]}
                self.assertLessEqual(types, built)


if __name__ == "__main__":
    unittest.main()
