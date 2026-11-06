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
            "images/cloud/linux/Dockerfile": (
                "FROM agent-base\nRUN bash /t/install-toolset.sh display\nRUN bash /t/install-toolset.sh go\n"
            ),
            "images/cloud/runner/Dockerfile": "FROM agent-tools\n",
            "images/toolsets/go/Brewfile": 'brew "go"\n',
            # linux's tools on bake target base-gpu (base on CUDA), then gpu.
            "images/cloud/linux-gpu/Dockerfile": "FROM agent-linux\nRUN bash /t/install-toolset.sh gpu\n",
            "images/cloud/orchestrator/Dockerfile": "FROM ubuntu:24.04\n",
            "images/cloud/entrypoint.sh": "#!/bin/bash\n",
            "images/shared/agent-runner.sh": "#!/bin/bash\n",
            "images/toolsets/core/packages.txt": "git\n",
            "images/toolsets/node/Brewfile": 'brew "node"\n',
            "images/toolsets/display/packages.txt": "xvfb\n",
            "images/toolsets/gpu/packages.txt": "virtualgl\n",
            "images/toolsets/android/packages.txt": "cpu-checker\n",
            "images/toolsets/install-toolset.sh": "#!/bin/bash\n",
            "images/toolsets/write-claude-md.sh": "#!/bin/bash\n",
            "images/toolsets/toolset-checks.sh": "#!/bin/bash\n",
            "images/android/toolsets.txt": "core\n",
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
        self.assertEqual(self.change("specs/gcp.md"), {"types": [], "vms": [], "orchestrator": False, "prune": False})

    def test_shared_files_rebuild_every_type(self):
        result = self.change("images/shared/agent-runner.sh")
        self.assertEqual(
            result["types"],
            [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}],
        )
        self.assertFalse(result["orchestrator"])

    def test_a_toolset_rebuilds_the_types_that_install_it(self):
        both = [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}]
        # base's, and linux's, which linux-gpu is built on.
        for name in ("images/toolsets/core/packages.txt", "images/toolsets/node/Brewfile",
                     "images/toolsets/go/Brewfile"):
            with self.subTest(name=name):
                self.assertEqual(self.change(name)["types"], both)
                self.base = self.head()

    def test_a_types_own_toolset_rebuilds_only_it(self):
        self.assertEqual(
            self.change("images/toolsets/gpu/packages.txt")["types"], [{"type": "linux-gpu", "build": True}]
        )

    def test_the_runner_stage_rebuilds_every_type(self):
        self.assertEqual(
            self.change("images/cloud/runner/Dockerfile")["types"],
            [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}],
        )

    def test_a_toolset_no_cloud_type_installs_deploys_nothing(self):
        self.assertEqual(
            self.change("images/toolsets/android/packages.txt"),
            {"types": [], "vms": [], "orchestrator": False, "prune": False},
        )

    def test_the_toolset_scripts_rebuild_every_type(self):
        both = [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}]
        self.assertEqual(self.change("images/toolsets/install-toolset.sh")["types"], both)
        self.base = self.head()
        self.assertEqual(self.change("images/toolsets/write-claude-md.sh")["types"], both)
        self.base = self.head()
        self.assertEqual(self.change("images/toolsets/toolset-checks.sh")["types"], both)

    def test_the_android_images_toolset_list_deploys_nothing(self):
        self.assertEqual(
            self.change("images/android/toolsets.txt"), {"types": [], "vms": [], "orchestrator": False, "prune": False}
        )

    def test_a_types_directory_rebuilds_it_and_the_types_built_on_it(self):
        result = self.change("images/cloud/linux-gpu/Dockerfile")
        self.assertEqual(
            result, {"types": [{"type": "linux-gpu", "build": True}], "vms": [], "orchestrator": False, "prune": False}
        )
        self.base = self.head()
        self.assertEqual(self.change("images/cloud/linux/Dockerfile")["types"], [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}])

    def test_base_rebuilds_every_type(self):
        self.assertEqual(self.change("images/cloud/base/Dockerfile")["types"], [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}])

    def test_a_type_on_base_gpu_reaches_base(self):
        # base-gpu is a bake target (base's Dockerfile on CUDA), with no directory.
        self.write("infra/gcp/3d-gpu.env", "GPU_TYPE=nvidia-l4\n")
        self.write("images/cloud/3d-gpu/Dockerfile", "FROM agent-base-gpu\nRUN bash /t/install-toolset.sh gpu\n")
        self.commit("3d-gpu")
        self.base = self.head()
        self.assertIn({"type": "3d-gpu", "build": True}, self.change("images/cloud/base/Dockerfile")["types"])
        self.base = self.head()
        self.assertIn({"type": "3d-gpu", "build": True}, self.change("images/toolsets/core/packages.txt")["types"])

    def test_an_env_file_rewrites_the_job_and_the_orchestrator(self):
        result = self.change("infra/gcp/linux.env")
        self.assertEqual(
            result, {"types": [{"type": "linux", "build": False}], "vms": [], "orchestrator": True, "prune": False}
        )

    def test_project_env_redeploys_everything(self):
        result = self.change("infra/gcp/project.env")
        self.assertEqual(len(result["types"]), 2)
        self.assertTrue(all(entry["build"] for entry in result["types"]))
        self.assertTrue(result["orchestrator"])

    def test_the_hook_redeploys_the_orchestrator_only(self):
        self.assertEqual(self.change("hooks/gcp/spawn-runner"), {"types": [], "vms": [], "orchestrator": True, "prune": False})

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
                "vms": [],
                "orchestrator": True,
                "prune": True,
            },
        )

    def test_schedule_unset_is_nightly(self):
        nightly = self.targets("--schedule", "nightly")
        self.assertEqual(nightly["types"], [{"type": "linux", "build": True}, {"type": "linux-gpu", "build": True}])
        self.assertTrue(nightly["orchestrator"])
        self.assertFalse(nightly["prune"])
        self.assertEqual(self.targets("--schedule", "weekly"), {"types": [], "vms": [], "orchestrator": False, "prune": False})

    def test_schedule_takes_the_types_class(self):
        self.write("infra/gcp/linux-gpu.env", "CPU=20\nREBUILD=weekly\n")
        self.assertEqual(self.targets("--schedule", "nightly")["types"], [{"type": "linux", "build": True}])
        weekly = self.targets("--schedule", "weekly")
        self.assertEqual(weekly["types"], [{"type": "linux-gpu", "build": True}])
        self.assertFalse(weekly["orchestrator"])

    def test_schedule_refuses_an_unknown_class(self):
        for args in (["--schedule", "monthly"], ["--schedule"], ["--schedule", "weekly", "extra"]):
            with self.subTest(args=args):
                result = subprocess.run(
                    ["bash", str(SCRIPT), *args], cwd=self.repo, capture_output=True, text=True, timeout=30
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage", result.stderr)

    def test_deleting_a_type_prunes_and_redeploys_the_orchestrator(self):
        self.git("rm", "-q", "infra/gcp/linux-gpu.env")
        self.commit("drop linux-gpu")
        result = self.targets()
        self.assertTrue(result["prune"])
        self.assertTrue(result["orchestrator"])
        self.assertEqual(result["types"], [])


class ComputeTypeTests(ChangedTargetsTests):
    """With a Compute Engine type (BACKEND=compute): android, built by Packer from images/android."""

    def setUp(self):
        super().setUp()
        self.write("infra/gcp/android.env", "BACKEND=compute\nMACHINE_TYPE=n2-standard-8\n")
        self.write("images/android/toolsets.txt", "# a comment\ncore\nandroid  # the SDK\n")
        self.write("images/android/gce/build.pkr.hcl", "build {}\n")
        self.write("images/android/scripts/setup-user.sh", "#!/bin/bash\n")
        self.commit("android")
        self.base = self.head()

    ANDROID = [{"type": "android"}]

    def test_shared_files_rebuild_every_type(self):
        result = self.change("images/shared/agent-runner.sh")
        self.assertEqual(len(result["types"]), 2)
        self.assertEqual(result["vms"], self.ANDROID)

    def test_its_own_directory_rebuilds_its_image_only(self):
        for name in ("images/android/gce/build.pkr.hcl", "images/android/scripts/setup-user.sh",
                     "images/android/toolsets.txt"):
            with self.subTest(name=name):
                self.assertEqual(
                    self.change(name), {"types": [], "vms": self.ANDROID, "orchestrator": False, "prune": False}
                )
                self.base = self.head()

    def test_the_toolset_scripts_rebuild_it_and_every_type(self):
        for name in ("images/toolsets/install-toolset.sh", "images/toolsets/toolset-checks.sh"):
            with self.subTest(name=name):
                result = self.change(name)
                self.assertEqual(result["vms"], self.ANDROID)
                self.assertEqual(len(result["types"]), 2)
                self.base = self.head()

    def test_its_toolsets_rebuild_it(self):
        self.assertEqual(self.change("images/toolsets/android/packages.txt")["vms"], self.ANDROID)
        self.base = self.head()
        result = self.change("images/toolsets/core/packages.txt")
        self.assertEqual(result["vms"], self.ANDROID)
        self.assertEqual(len(result["types"]), 2)

    def test_a_toolset_it_does_not_install_leaves_it(self):
        result = self.change("images/toolsets/go/Brewfile")
        self.assertEqual(len(result["types"]), 2)
        self.assertEqual(result["vms"], [])

    def test_the_cloud_run_images_leave_it(self):
        for name in ("images/cloud/runner/Dockerfile", "images/cloud/entrypoint.sh", "scripts/cloud/job-spec.sh"):
            with self.subTest(name=name):
                self.assertEqual(self.change(name)["vms"], [])
                self.base = self.head()

    def test_its_env_file_redeploys_the_orchestrator_only(self):
        self.assertEqual(
            self.change("infra/gcp/android.env"), {"types": [], "vms": [], "orchestrator": True, "prune": False}
        )

    def test_project_env_redeploys_everything(self):
        result = self.change("infra/gcp/project.env")
        self.assertEqual(len(result["types"]), 2)
        self.assertEqual(result["vms"], self.ANDROID)

    def test_all(self):
        result = self.targets("--all")
        self.assertEqual(result["vms"], self.ANDROID)
        self.assertNotIn("android", [entry["type"] for entry in result["types"]])

    def test_schedule_puts_it_in_vms(self):
        self.write("infra/gcp/android.env", "BACKEND=compute\nREBUILD=weekly\n")
        self.assertEqual(
            self.targets("--schedule", "weekly"), {"types": [], "vms": self.ANDROID, "orchestrator": False, "prune": False}
        )
        self.assertEqual(self.targets("--schedule", "nightly")["vms"], [])

    # Inherited ones whose fixture now includes android's toolsets.
    def test_a_toolset_no_cloud_type_installs_deploys_nothing(self):
        self.assertEqual(self.change("images/toolsets/gpu/packages.txt")["vms"], [])

    @unittest.skip("with android as a compute type it rebuilds android; test_its_own_directory_rebuilds_its_image_only")
    def test_the_android_images_toolset_list_deploys_nothing(self):
        pass

    def test_no_usable_base_means_everything(self):
        result = self.targets("", self.head())
        self.assertEqual(result["vms"], self.ANDROID)


if __name__ == "__main__":
    unittest.main()
