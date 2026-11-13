"""scripts/cloud/lib.sh and hooks/gcp/lib.sh's per-type settings: one reader each
for what a type is built from and how it's configured (AGENTS.md, "One source
per fact")."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO


def run(cwd: Path, script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{REPO}/scripts/cloud/lib.sh"; AGENT_GCP_ENV_DIR=infra/gcp; {script}'],
        cwd=cwd, capture_output=True, text=True,
    )


class CloudToolsetsTests(unittest.TestCase):
    def test_linux_gpu_installs_base_then_linux_then_gpu(self):
        """linux-gpu is linux's Dockerfile on base-gpu, then gpu: the real chain."""
        result = run(REPO, "cloud_toolsets linux-gpu")
        self.assertEqual(result.returncode, 0, result.stderr)
        toolsets = result.stdout.split()
        self.assertEqual(toolsets[0], "core")
        self.assertEqual(toolsets[-1], "gpu")
        self.assertLess(toolsets.index("native"), toolsets.index("go"))
        self.assertEqual(len(toolsets), len(set(toolsets)))

    def test_order_is_root_first_and_file_order_within(self):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        files = {
            "images/cloud/base/Dockerfile": "FROM ubuntu\nRUN bash /t/install-toolset.sh core\n",
            "images/cloud/mid/Dockerfile": (
                "FROM agent-base\nRUN bash /t/install-toolset.sh b\nRUN bash /t/install-toolset.sh a\n"
            ),
            "images/cloud/top/Dockerfile": "FROM agent-mid\nRUN bash /t/install-toolset.sh z\n",
        }
        for name, text in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        result = run(repo, "cloud_toolsets top")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["core", "b", "a", "z"])

    def test_base_gpu_maps_to_base(self):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        for name, text in {
            "images/cloud/base/Dockerfile": "FROM ubuntu\nRUN bash /t/install-toolset.sh core\n",
            "images/cloud/g/Dockerfile": "FROM agent-base-gpu\nRUN bash /t/install-toolset.sh gpu\n",
        }.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        result = run(repo, "cloud_chain g; cloud_toolsets g")
        self.assertEqual(result.stdout.split(), ["g", "base", "core", "gpu"])


class TypeSettingTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        env = self.repo / "infra/gcp"
        env.mkdir(parents=True)
        (env / "project.env").write_text("PROJECT_ID=p\n")
        (env / "plain.env").write_text("CPU=4\n")
        (env / "weekly.env").write_text("REBUILD=weekly\nBACKEND=compute\nDEPLOY=false\n")
        (env / "empty.env").write_text("REBUILD=\n")

    def value(self, script):
        result = run(self.repo, script)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_defaults_when_unset_or_empty(self):
        self.assertEqual(self.value("gcp_rebuild_class plain"), "nightly")
        self.assertEqual(self.value("gcp_rebuild_class empty"), "nightly")
        self.assertEqual(self.value("gcp_backend plain"), "run")

    def test_values_from_the_env_file(self):
        self.assertEqual(self.value("gcp_rebuild_class weekly"), "weekly")
        self.assertEqual(self.value("gcp_backend weekly"), "compute")

    def test_deploys(self):
        self.assertEqual(self.value("gcp_deploys plain && echo yes || echo no").strip(), "yes")
        self.assertEqual(self.value("gcp_deploys weekly && echo yes || echo no").strip(), "no")

    def test_missing_env_file_prints_nothing(self):
        self.assertEqual(self.value("gcp_type_setting gone REBUILD nightly"), "")

    def test_settings_do_not_leak_into_the_caller(self):
        self.assertEqual(self.value('gcp_rebuild_class weekly >/dev/null; echo "${REBUILD:-unset}"').strip(), "unset")


class CheckTypeTests(unittest.TestCase):
    """gcp_check_type: the one reader of the DEPLOY=false-on-compute rule."""

    def check(self, env_dir: Path, type_: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", f'set -euo pipefail; source "{REPO}/hooks/gcp/lib.sh"; gcp_check_type {type_}'],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "AGENT_GCP_ENV_DIR": str(env_dir)},
        )

    def fixture(self, **files: str) -> Path:
        env_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, env_dir)
        (env_dir / "project.env").write_text("PROJECT_ID=p\n")
        for name, text in files.items():
            (env_dir / f"{name}.env").write_text(text)
        return env_dir

    def test_refuses_deploy_false_on_a_compute_type_with_one_message(self):
        env_dir = self.fixture(vm="BACKEND=compute\nDEPLOY=false\n")
        result = self.check(env_dir, "vm")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count("\n"), 1)
        self.assertIn("vm: DEPLOY=false is for Cloud Run types only", result.stderr)
        self.assertIn("remove it from infra/gcp/vm.env", result.stderr)

    def test_accepts_the_other_combinations(self):
        env_dir = self.fixture(
            run_false="DEPLOY=false\n", run_true="DEPLOY=true\n", vm="BACKEND=compute\n", vm_true="BACKEND=compute\nDEPLOY=true\n"
        )
        for type_ in ("run_false", "run_true", "vm", "vm_true"):
            with self.subTest(type=type_):
                result = self.check(env_dir, type_)
                self.assertEqual((result.returncode, result.stderr), (0, ""))

    def refused_types(self, env_dir: Path) -> dict[str, str]:
        """Every type in env_dir that gcp_check_type refuses, with its message."""
        listed = subprocess.run(
            ["bash", "-c", f'source "{REPO}/hooks/gcp/lib.sh"; gcp_types'],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "AGENT_GCP_ENV_DIR": str(env_dir)},
        )
        types = listed.stdout.split()
        self.assertTrue(types, "no runner types found")
        results = {type_: self.check(env_dir, type_) for type_ in types}
        return {type_: result.stderr for type_, result in results.items() if result.returncode != 0}

    def test_the_real_env_files_pass(self):
        """A bad infra/gcp/*.env fails its pull request here, not on the host (r3.f2)."""
        self.assertEqual(self.refused_types(REPO / "infra/gcp"), {})

    def test_the_real_env_files_check_goes_red_on_a_bad_compute_file(self):
        env_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, env_dir)
        shutil.copytree(REPO / "infra/gcp", env_dir, dirs_exist_ok=True)
        with (env_dir / "android.env").open("a") as file:
            file.write("DEPLOY=false\n")
        refused = self.refused_types(env_dir)
        self.assertEqual(list(refused), ["android"])
        self.assertIn("DEPLOY=false is for Cloud Run types only", refused["android"])


if __name__ == "__main__":
    unittest.main()
