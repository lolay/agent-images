"""scripts/cloud/changed-targets.sh maps a diff to what redeploys."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO

SCRIPT = REPO / "scripts/cloud/changed-targets.sh"


class ChangedTargetsTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        files = {
            "infra/gcp/project.env": "PROJECT_ID=p\n",
            "infra/gcp/linux.env": "CPU=4\n",
            "infra/gcp/linux-gpu.env": "CPU=20\n",
            "images/cloud/base/Dockerfile": (
                "FROM ubuntu:24.04\n"
                "RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/t/install-toolset.sh \\\n"
                "\tbash /t/install-toolset.sh core\n"
                "RUN bash /t/install-toolset.sh node\n"
            ),
            "images/cloud/base-gpu/Dockerfile": "ARG BASE\nFROM agent-base\nRUN bash /t/install-toolset.sh display\n",
            "images/cloud/linux/Dockerfile": "FROM agent-base\n",
            "images/cloud/linux-gpu/Dockerfile": "FROM agent-base-gpu\n",
            "images/cloud/orchestrator/Dockerfile": "FROM ubuntu:24.04\n",
            "images/cloud/entrypoint.sh": "#!/bin/bash\n",
            "images/shared/agent-runner.sh": "#!/bin/bash\n",
            "images/toolsets/core/packages.txt": "git\n",
            "images/toolsets/node/Brewfile": 'brew "node"\n',
            "images/toolsets/display/packages.txt": "xvfb\n",
            "images/toolsets/android/packages.txt": "cpu-checker\n",
            "images/toolsets/install-toolset.sh": "#!/bin/bash\n",
            "images/toolsets/write-claude-md.sh": "#!/bin/bash\n",
            "images/linux/toolsets.txt": "core\n",
            "hooks/gcp/spawn-runner": "#!/bin/bash\n",
            "scripts/cloud/job-spec.sh": "#!/bin/bash\n",
            "specs/gcp.md": "# GCP\n",
        }
        for name, text in files.items():
            self.write(name, text)
        self.commit("first")
        self.base = self.head()

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.repo)], check=True)

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def head(self):
        return self.git("rev-parse", "HEAD")

    def targets(self, *args):
        result = subprocess.run(
            ["bash", str(SCRIPT), *(args or (self.base, self.head()))],
            cwd=self.repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def change(self, *names):
        for name in names:
            self.write(name, (self.repo / name).read_text() + "# changed\n" if (self.repo / name).exists() else "new\n")
        self.commit("change")
        return self.targets()

    def test_docs_deploy_nothing(self):
        self.assertEqual(self.change("specs/gcp.md"), {"types": [], "orchestrator": False, "prune": False})

    def test_shared_files_rebuild_every_type(self):
        result = self.change("images/shared/agent-runner.sh")
        self.assertEqual(
            result["types"],
            [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}],
        )
        self.assertFalse(result["orchestrator"])

    def test_a_toolset_rebuilds_the_types_that_install_it(self):
        both = [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}]
        self.assertEqual(self.change("images/toolsets/core/packages.txt")["types"], both)
        self.base = self.head()
        self.assertEqual(self.change("images/toolsets/node/Brewfile")["types"], both)
        self.base = self.head()
        self.assertEqual(
            self.change("images/toolsets/display/packages.txt")["types"],
            [{"type": "linux-gpu", "build": True}],
        )

    def test_a_toolset_no_cloud_type_installs_deploys_nothing(self):
        self.assertEqual(
            self.change("images/toolsets/android/packages.txt"),
            {"types": [], "orchestrator": False, "prune": False},
        )

    def test_the_toolset_scripts_rebuild_every_type(self):
        both = [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}]
        self.assertEqual(self.change("images/toolsets/install-toolset.sh")["types"], both)
        self.base = self.head()
        self.assertEqual(self.change("images/toolsets/write-claude-md.sh")["types"], both)

    def test_the_android_images_toolset_list_deploys_nothing(self):
        self.assertEqual(
            self.change("images/linux/toolsets.txt"), {"types": [], "orchestrator": False, "prune": False}
        )

    def test_a_types_directory_rebuilds_only_it(self):
        result = self.change("images/cloud/linux/Dockerfile")
        self.assertEqual(
            result, {"types": [{"type": "linux", "build": True}], "orchestrator": False, "prune": False}
        )

    def test_a_build_parent_rebuilds_its_children_up_the_chain(self):
        self.assertEqual(
            self.change("images/cloud/base-gpu/Dockerfile")["types"],
            [{"type": "linux-gpu", "build": True}],
        )
        self.base = self.head()
        self.assertEqual(
            self.change("images/cloud/base/Dockerfile")["types"],
            [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}],
        )

    def test_an_env_file_rewrites_the_job_and_the_orchestrator(self):
        result = self.change("infra/gcp/linux.env")
        self.assertEqual(
            result, {"types": [{"type": "linux", "build": False}], "orchestrator": True, "prune": False}
        )

    def test_project_env_redeploys_everything(self):
        result = self.change("infra/gcp/project.env")
        self.assertEqual(len(result["types"]), 2)
        self.assertTrue(all(entry["build"] for entry in result["types"]))
        self.assertTrue(result["orchestrator"])

    def test_the_hook_redeploys_the_orchestrator_only(self):
        self.assertEqual(self.change("hooks/gcp/spawn-runner"), {"types": [], "orchestrator": True, "prune": False})

    def test_the_job_spec_rewrites_every_job(self):
        result = self.change("scripts/cloud/job-spec.sh")
        self.assertEqual(
            result["types"],
            [{"type": "linux", "build": False}, {"type": "linux-gpu", "build": False}],
        )

    def test_no_usable_base_means_everything(self):
        for base in ("", "0" * 40):
            result = self.targets(base, self.head())
            self.assertEqual(len(result["types"]), 2)
            self.assertTrue(result["orchestrator"])

    def test_all(self):
        result = self.targets("--all")
        self.assertEqual(
            result,
            {
                "types": [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}],
                "orchestrator": True,
                "prune": True,
            },
        )

    def test_deleting_a_type_prunes_and_redeploys_the_orchestrator(self):
        self.git("rm", "-q", "infra/gcp/linux-gpu.env")
        self.commit("drop linux-gpu")
        result = self.targets()
        self.assertTrue(result["prune"])
        self.assertTrue(result["orchestrator"])
        self.assertEqual(result["types"], [])


if __name__ == "__main__":
    unittest.main()
