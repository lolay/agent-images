"""scripts/cloud/job-spec.sh renders the linux job from the committed env files."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO

SCRIPT = REPO / "scripts/cloud/job-spec.sh"
IMAGE = "us-central1-docker.pkg.dev/lolay-agents/agent-images/agent-linux@sha256:" + "a" * 64


def render(*args, env_dir=None):
    env = {"PATH": "/usr/bin:/bin"}
    if env_dir:
        env["AGENT_GCP_ENV_DIR"] = str(env_dir)
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30
    )


class JobSpecTests(unittest.TestCase):
    def job(self, *extra):
        result = render("linux", IMAGE, *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_names_the_job_and_labels_it(self):
        job = self.job("--sha", "0123456789abcdef")
        self.assertEqual(job["name"], "projects/lolay-agents/locations/us-central1/jobs/agent-linux")
        self.assertEqual(
            job["labels"],
            {"managed-by": "agent-images", "agent-images-type": "linux", "agent-images-sha": "0123456789abcdef"},
        )
        self.assertNotIn("launchStage", job)

    def test_next_job_takes_the_same_spec_under_its_own_name(self):
        job = self.job("--name", "agent-linux-next")
        self.assertTrue(job["name"].endswith("/jobs/agent-linux-next"))
        self.assertEqual(job["labels"]["agent-images-type"], "linux")

    def test_one_task_no_retries_on_the_runner_account(self):
        job = self.job()
        self.assertEqual(job["template"]["taskCount"], 1)
        task = job["template"]["template"]
        self.assertEqual(task["maxRetries"], 0)
        self.assertEqual(task["timeout"], "34200s")
        self.assertEqual(task["serviceAccount"], "agent-runner@lolay-agents.iam.gserviceaccount.com")
        self.assertEqual(task["executionEnvironment"], "EXECUTION_ENVIRONMENT_GEN2")
        container = task["containers"][0]
        self.assertEqual(container["name"], "runner", "the hook's overrides name it")
        self.assertEqual(container["image"], IMAGE)
        self.assertEqual(container["resources"]["limits"], {"cpu": "4", "memory": "16Gi"})

    def test_scratch_is_a_disk_and_the_buckets_are_mounted(self):
        task = self.job()["template"]["template"]
        volumes = {volume["name"]: volume for volume in task["volumes"]}
        self.assertEqual(volumes["scratch"]["emptyDir"], {"medium": "DISK", "sizeLimit": "10Gi"})
        self.assertEqual(volumes["outputs"]["gcs"]["bucket"], "lolay-agents-outputs")
        self.assertFalse(volumes["outputs"]["gcs"]["readOnly"])
        self.assertEqual(volumes["outputs"]["gcs"]["mountOptions"], ["implicit-dirs"])
        self.assertEqual(volumes["datasets"]["gcs"]["bucket"], "lolay-agents-datasets")
        self.assertTrue(volumes["datasets"]["gcs"]["readOnly"])
        mounts = {mount["name"]: mount["mountPath"] for mount in task["containers"][0]["volumeMounts"]}
        self.assertEqual(
            mounts, {"scratch": "/scratch", "outputs": "/mnt/outputs", "datasets": "/mnt/datasets"}
        )

    def test_passes_the_runner_settings_as_env(self):
        container = self.job()["template"]["template"]["containers"][0]
        env = {item["name"]: item["value"] for item in container["env"]}
        self.assertEqual(
            env,
            {
                "AGENT_TYPE": "linux",
                "AGENT_TASK_TIMEOUT_SEC": "34200",
                "AGENT_RETIRE_MARGIN_SEC": "900",
                "AGENT_NOTICE_TZ": "America/Los_Angeles",
                "AGENT_OUTPUTS_BUCKET": "lolay-agents-outputs",
                "CLAUDE_EXIT_IF_UNUSED_MIN": "60",
                "CLAUDE_RELEASE_IDLE_SESSION_MIN": "60",
                "CLAUDE_KILL_SESSION_AFTER_MIN": "480",
                "CLAUDE_UPGRADE": "false",
            },
        )

    # D6: a nightly type runs the Claude Code its lock baked in; a weekly one
    # keeps updating itself at start. The class is read where the rebuild
    # schedule reads it (gcp_rebuild_class), with its one default.
    def test_claude_self_update_follows_the_rebuild_class(self):
        cases = {"REBUILD=nightly": "false", "REBUILD=weekly": None, None: "false"}
        for line, expected in cases.items():
            with self.subTest(line):
                env = self.custom_env([line] if line else [], drop=("REBUILD=",))
                self.assertEqual(env.get("CLAUDE_UPGRADE"), expected)

    def test_gis_updates_claude_itself(self):
        result = render("gis", IMAGE)
        self.assertEqual(result.returncode, 0, result.stderr)
        container = json.loads(result.stdout)["template"]["template"]["containers"][0]
        self.assertNotIn("CLAUDE_UPGRADE", {item["name"] for item in container["env"]})

    def test_launch_stage_only_when_the_type_sets_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_dir = Path(tmp)
            (env_dir / "project.env").write_text((REPO / "infra/gcp/project.env").read_text())
            (env_dir / "linux.env").write_text(
                (REPO / "infra/gcp/linux.env").read_text() + "LAUNCH_STAGE=BETA\n"
            )
            result = render("linux", IMAGE, env_dir=env_dir)
        self.assertEqual(json.loads(result.stdout)["launchStage"], "BETA")

    def custom_job(self, extra, drop=()):
        with tempfile.TemporaryDirectory() as tmp:
            env_dir = Path(tmp)
            (env_dir / "project.env").write_text((REPO / "infra/gcp/project.env").read_text())
            lines = [
                line for line in (REPO / "infra/gcp/linux.env").read_text().splitlines()
                if not line.startswith(("EXIT_IF_UNUSED_MIN=", "RELEASE_IDLE_SESSION_MIN=",
                                        "KILL_SESSION_AFTER_MIN=", "RETIRE_MARGIN_SEC=") + tuple(drop))
            ]
            (env_dir / "linux.env").write_text("\n".join(lines + extra) + "\n")
            return render("linux", IMAGE, env_dir=env_dir)

    def custom_env(self, extra, drop=()):
        result = self.custom_job(extra, drop)
        self.assertEqual(result.returncode, 0, result.stderr)
        container = json.loads(result.stdout)["template"]["template"]["containers"][0]
        return {item["name"]: item["value"] for item in container["env"]}

    def test_unset_runner_settings_are_left_to_claude_sh(self):
        env = self.custom_env([])
        for name in ("CLAUDE_EXIT_IF_UNUSED_MIN", "CLAUDE_RELEASE_IDLE_SESSION_MIN",
                     "CLAUDE_KILL_SESSION_AFTER_MIN"):
            self.assertNotIn(name, env)
        self.assertEqual(env["AGENT_RETIRE_MARGIN_SEC"], "900")

    def test_empty_runner_settings_pass_through_as_never(self):
        env = self.custom_env(["KILL_SESSION_AFTER_MIN=", "RETIRE_MARGIN_SEC=600"])
        self.assertEqual(env["CLAUDE_KILL_SESSION_AFTER_MIN"], "")
        self.assertEqual(env["AGENT_RETIRE_MARGIN_SEC"], "600")

    def test_refuses_a_margin_not_less_than_the_timeout(self):
        result = self.custom_job(["RETIRE_MARGIN_SEC=34200"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("RETIRE_MARGIN_SEC is not less than TASK_TIMEOUT_SEC", result.stderr)

    def gpu_job(self, gpu_type):
        with tempfile.TemporaryDirectory() as tmp:
            env_dir = Path(tmp)
            (env_dir / "project.env").write_text((REPO / "infra/gcp/project.env").read_text())
            (env_dir / "linux-gpu.env").write_text(
                (REPO / "infra/gcp/linux.env").read_text() + f"GPU_TYPE={gpu_type}\n"
            )
            return render("linux-gpu", IMAGE, env_dir=env_dir)

    def test_a_cpu_type_asks_for_no_gpu(self):
        task = self.job()["template"]["template"]
        self.assertNotIn("nodeSelector", task)
        self.assertNotIn("gpuZonalRedundancyDisabled", task)
        self.assertNotIn("nvidia.com/gpu", task["containers"][0]["resources"]["limits"])

    def test_gpu_type_adds_one_gpu_without_zonal_redundancy(self):
        for gpu_type in ("nvidia-l4", "nvidia-rtx-pro-6000"):
            with self.subTest(gpu_type=gpu_type):
                result = self.gpu_job(gpu_type)
                self.assertEqual(result.returncode, 0, result.stderr)
                task = json.loads(result.stdout)["template"]["template"]
                self.assertEqual(task["nodeSelector"], {"accelerator": gpu_type})
                self.assertIs(task["gpuZonalRedundancyDisabled"], True)
                self.assertEqual(
                    task["containers"][0]["resources"]["limits"],
                    {"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"},
                )
                # The rest of the task is the CPU type's.
                self.assertEqual(task["maxRetries"], 0)
                self.assertEqual(len(task["volumes"]), 3)

    def test_refuses_an_unknown_gpu_type(self):
        result = self.gpu_job("nvidia-14")
        self.assertEqual(result.returncode, 2)
        self.assertIn("GPU_TYPE nvidia-14", result.stderr)

    def test_refuses_a_tag(self):
        result = render("linux", IMAGE.split("@")[0] + ":latest")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a digest", result.stderr)

    def test_refuses_an_unknown_type(self):
        result = render("nosuchtype", IMAGE)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
