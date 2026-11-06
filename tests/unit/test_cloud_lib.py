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


if __name__ == "__main__":
    unittest.main()
