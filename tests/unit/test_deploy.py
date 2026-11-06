"""scripts/cloud/deploy.sh against fake GCP endpoints and a fake gcloud."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from fake_gcp import TOKEN, FakeGcp
from support import REPO, base_env, write_executable

SCRIPT = REPO / "scripts/cloud/deploy.sh"
REGISTRY = "us-central1-docker.pkg.dev/lolay-agents/agent-images"
IMAGE = f"{REGISTRY}/agent-linux@sha256:" + "b" * 64
JOBS = "projects/lolay-agents/locations/us-central1/jobs"

FAKE_GCLOUD = f"""
printf '%s\\n' "$*" >>"$FAKE_OUT/gcloud.log"
case "$1 $2" in
"auth print-access-token") echo {TOKEN} ;;
"run jobs")
	case "$3" in
	execute) exit "${{FAKE_SMOKE_STATUS:-0}}" ;;
	executions) echo agent-linux-next-p5rqf ;;
	esac ;;
"logging read")
	[[ -n "${{FAKE_LOG_DENIED:-}}" ]] && {{ echo "PERMISSION_DENIED" >&2; exit 1; }}
	printf 'smoke: pass claude (2.1.318)\nsmoke: FAIL scratch (not a mount)\n' ;;
"compute instances")
	if [[ "$3" == describe ]]; then
		echo '{{"metadata": {{"items": [{{"key": "orchestrator-image", "value": "{REGISTRY}/agent-orchestrator@sha256:cc"}}]}}}}'
	fi ;;
esac
"""


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        self.out.mkdir()
        write_executable(self.tmp / "bin/gcloud", FAKE_GCLOUD)
        self.gcp = FakeGcp().__enter__()

    def tearDown(self):
        self.gcp.__exit__(None, None, None)
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def deploy(self, *args, **env):
        settings = {**self.gcp.env(), "FAKE_OUT": str(self.out), "DEPLOY_POLL_SEC": "0"}
        settings.update(env)
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=base_env(self.tmp, self.tmp / "bin", **settings),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def gcloud_calls(self):
        log = self.out / "gcloud.log"
        lines = log.read_text().splitlines() if log.exists() else []
        return [line for line in lines if not line.startswith("auth ")]

    def job_patches(self):
        return [request.path.rsplit("/", 1)[1] for request in self.gcp.requests("PATCH", "/jobs/")]

    def test_type_writes_next_smokes_it_then_promotes(self):
        result = self.deploy("type", "linux", IMAGE, "abc123")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.job_patches(), ["agent-linux-next", "agent-linux"])
        self.assertEqual(
            self.gcloud_calls(),
            ["run jobs execute agent-linux-next --project lolay-agents --region us-central1 --args=smoke --wait --quiet"],
        )
        for name in ("agent-linux", "agent-linux-next"):
            job = self.gcp.state.jobs[f"{JOBS}/{name}"]
            self.assertEqual(job["template"]["template"]["containers"][0]["image"], IMAGE)
            self.assertEqual(job["labels"]["agent-images-sha"], "abc123")
        # Each write's operation was polled until done.
        self.assertEqual(len(self.gcp.requests("GET", "/operations/")), 2)

    def test_a_failed_smoke_leaves_the_live_job_alone(self):
        result = self.deploy("type", "linux", IMAGE, "abc123", FAKE_SMOKE_STATUS="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("agent-linux keeps its image", result.stderr)
        self.assertEqual(self.job_patches(), ["agent-linux-next"])

    def test_a_failed_smoke_prints_the_executions_log(self):
        result = self.deploy("type", "linux", IMAGE, "abc123", FAKE_SMOKE_STATUS="1")
        self.assertIn("deploy: agent-linux-next-p5rqf log:", result.stdout)
        self.assertIn("  smoke: FAIL scratch (not a mount)", result.stdout)
        read = [call for call in self.gcloud_calls() if call.startswith("logging read")][0]
        self.assertIn('labels."run.googleapis.com/execution_name"="agent-linux-next-p5rqf"', read)

    def test_an_unreadable_log_says_how_to_read_it(self):
        result = self.deploy(
            "type", "linux", IMAGE, "abc123", FAKE_SMOKE_STATUS="1", FAKE_LOG_DENIED="1"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("can't read its log here", result.stdout)
        self.assertIn("run: gcloud logging read", result.stdout)

    def test_live_reuses_the_current_image(self):
        self.assertEqual(self.deploy("type", "linux", IMAGE, "first").returncode, 0)
        result = self.deploy("type", "linux", "--live", "second")
        self.assertEqual(result.returncode, 0, result.stderr)
        job = self.gcp.state.jobs[f"{JOBS}/agent-linux"]
        self.assertEqual(job["template"]["template"]["containers"][0]["image"], IMAGE)
        self.assertEqual(job["labels"]["agent-images-sha"], "second")

    def test_live_without_a_job_fails(self):
        result = self.deploy("type", "linux", "--live", "abc")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no live agent-linux", result.stderr)

    def test_type_refuses_an_image_outside_the_registry(self):
        result = self.deploy("type", "linux", "docker.io/library/ubuntu@sha256:" + "c" * 64, "abc")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.gcp.requests("PATCH"), [])

    def test_orchestrator_sets_the_vm_metadata(self):
        image = f"{REGISTRY}/agent-orchestrator@sha256:" + "d" * 64
        result = self.deploy("orchestrator", image)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.gcloud_calls(),
            [
                "compute instances add-metadata agent-orchestrator --project lolay-agents "
                f"--zone us-central1-a --metadata orchestrator-image={image} --quiet"
            ],
        )

    def test_orchestrator_refuses_a_tag(self):
        result = self.deploy("orchestrator", f"{REGISTRY}/agent-orchestrator:latest")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.gcloud_calls(), [])

    def test_prune_deletes_only_our_jobs_whose_type_is_gone(self):
        def job(name, labels):
            self.gcp.state.jobs[f"{JOBS}/{name}"] = {"name": f"{JOBS}/{name}", "labels": labels}

        ours = {"managed-by": "agent-images"}
        job("agent-linux", {**ours, "agent-images-type": "linux"})
        job("agent-linux-next", {**ours, "agent-images-type": "linux"})
        job("agent-retired", {**ours, "agent-images-type": "retired"})
        job("agent-retired-next", {**ours, "agent-images-type": "retired"})
        job("agent-other", {"agent-images-type": "other"})
        job("something-else", {})
        result = self.deploy("prune")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(name.rsplit("/", 1)[1] for name in self.gcp.state.jobs),
            ["agent-linux", "agent-linux-next", "agent-other", "something-else"],
        )

    def test_status_reads_jobs_and_the_vm(self):
        self.assertEqual(self.deploy("type", "linux", IMAGE, "abc123def456789").returncode, 0)
        result = self.deploy("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agent-linux ", result.stdout)
        self.assertIn("commit abc123def456", result.stdout)
        self.assertIn("agent-orchestrator     agent-orchestrator@sha256:cc", result.stdout)
        self.assertEqual(self.gcp.requests("PATCH", "/jobs/")[2:], [], "status writes nothing")


FAKE_PACKER = """
printf '%s\\n' "$*" >>"$FAKE_OUT/packer.log"
exit "${FAKE_PACKER_STATUS:-0}"
"""

ANDROID_IMAGE = "agent-android-20261106-abc123def456"
SMOKE = "agent-android-smoke-abc123def456"
ZONE_PATH = "/compute/v1/projects/lolay-agents/zones/us-central1-a"


def image(name, family, created="2026-11-06T10:00:00.000-08:00", type_="android"):
    return {
        "name": name,
        "family": family,
        "creationTimestamp": created,
        "labels": {"managed-by": "agent-images", "agent-images-type": type_, "agent-images-sha": "abc123def456"},
    }


class VmDeployTests(DeployTests):
    def setUp(self):
        super().setUp()
        write_executable(self.tmp / "bin/packer", FAKE_PACKER)
        self.gcp.state.images[ANDROID_IMAGE] = image(ANDROID_IMAGE, "agent-android-next")
        self.gcp.state.images["agent-android-older"] = image("agent-android-older", "agent-android")
        self.gcp.state.images["agent-android-failed"] = image("agent-android-failed", "agent-android-next")

    def deploy(self, *args, **env):
        return super().deploy(*args, **{"GCP_POLL_SEC": "0", "VM_SMOKE_POLL_SEC": "0.1", **env})

    def smoke_reports(self, result="pass"):
        self.gcp.state.instance_guest_attributes[SMOKE] = {
            "agent-images/emulator-boot-sec-cold": "41.2\n",
            "agent-images/emulator-boot-sec-warm": "9.8\n",
            "agent-images/smoke-log": "ok    claude: 2.1.400\nok    kvm: KVM acceleration can be used\n",
            "agent-images/smoke": f"{result}\n",
        }

    def test_vm_build_runs_packer_with_the_type_settings(self):
        result = self.deploy("vm-build", "android", ANDROID_IMAGE, "abc123def456")
        self.assertEqual(result.returncode, 0, result.stderr)
        init, build = (self.out / "packer.log").read_text().splitlines()
        self.assertEqual(init, f"init {REPO}/images/android/gce")
        for expected in (
            "build -force -color=false",
            "-var project_id=lolay-agents",
            "-var zone=us-central1-a",
            "-var subnetwork=projects/lolay-agents/regions/us-central1/subnetworks/agents-us-central1",
            f"-var image_name={ANDROID_IMAGE}",
            "-var image_family=agent-android-next",
            "-var commit=abc123def456",
            "-var machine_type=n2-standard-8",
            "-var disk_size_gb=100",
        ):
            self.assertIn(expected, build)
        self.assertTrue(build.endswith(f"{REPO}/images/android/gce"))

    def test_vm_build_fails_with_packer(self):
        result = self.deploy("vm-build", "android", ANDROID_IMAGE, "abc", FAKE_PACKER_STATUS="1")
        self.assertEqual(result.returncode, 1)

    def test_vm_commands_refuse_a_cloud_run_type(self):
        for command in ("vm-build", "vm-promote"):
            result = self.deploy(command, "linux", ANDROID_IMAGE, "abc")
            self.assertEqual(result.returncode, 1)
            self.assertIn("isn't a Compute Engine type", result.stderr)

    def test_type_refuses_a_compute_type(self):
        result = self.deploy("type", "android", IMAGE, "abc")
        self.assertEqual(result.returncode, 1)
        self.assertIn("deploys with vm-build and vm-promote", result.stderr)

    def test_vm_promote_smokes_then_moves_the_family_and_keeps_one(self):
        self.smoke_reports("pass")
        summary = self.tmp / "summary.md"
        result = self.deploy("vm-promote", "android", ANDROID_IMAGE, "abc123def456789",
                             GITHUB_STEP_SUMMARY=str(summary))
        self.assertEqual(result.returncode, 0, result.stderr)
        insert = self.gcp.requests("POST", f"{ZONE_PATH}/instances")[0].json()
        self.assertEqual(insert["name"], SMOKE)
        self.assertEqual(insert["serviceAccounts"], [], "the smoke VM gets no GCP access")
        self.assertEqual(insert["disks"][0]["initializeParams"]["sourceImage"],
                         f"projects/lolay-agents/global/images/{ANDROID_IMAGE}")
        self.assertEqual(insert["advancedMachineFeatures"], {"enableNestedVirtualization": True})
        metadata = {item["key"]: item["value"] for item in insert["metadata"]["items"]}
        self.assertEqual(metadata["agent-smoke"], "true")
        self.assertNotIn("work-order", metadata)
        self.assertNotIn(SMOKE, self.gcp.state.instances, "deleted afterwards")
        self.assertEqual(list(self.gcp.state.images), [ANDROID_IMAGE], "keep one")
        self.assertEqual(self.gcp.state.images[ANDROID_IMAGE]["family"], "agent-android")
        self.assertIn("  ok    kvm: KVM acceleration can be used", result.stdout)
        self.assertIn("| emulator-boot-sec-cold | 41.2 |", summary.read_text())

    def test_a_failed_smoke_leaves_the_family_alone(self):
        self.smoke_reports("fail")
        result = self.deploy("vm-promote", "android", ANDROID_IMAGE, "abc123def456789")
        self.assertEqual(result.returncode, 1)
        self.assertIn("agent-android keeps its image", result.stderr)
        self.assertEqual(self.gcp.state.images[ANDROID_IMAGE]["family"], "agent-android-next")
        self.assertIn("agent-android-older", self.gcp.state.images)
        self.assertNotIn(SMOKE, self.gcp.state.instances, "deleted even so")

    def test_no_smoke_result_times_out(self):
        result = self.deploy("vm-promote", "android", ANDROID_IMAGE, "abc123def456789", VM_SMOKE_TIMEOUT_SEC="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no smoke result", result.stderr)
        self.assertEqual(self.gcp.state.images[ANDROID_IMAGE]["family"], "agent-android-next")
        self.assertNotIn(SMOKE, self.gcp.state.instances)

    def test_a_leftover_smoke_vm_is_replaced(self):
        self.gcp.state.instances[SMOKE] = {"name": SMOKE, "metadata": {}}
        self.smoke_reports("pass")
        result = self.deploy("vm-promote", "android", ANDROID_IMAGE, "abc123def456789")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.gcp.requests("DELETE", f"/instances/{SMOKE}")), 2)

    def test_prune_deletes_images_whose_type_is_gone(self):
        self.gcp.state.images["agent-retired-1"] = image("agent-retired-1", "agent-retired", type_="retired")
        result = self.deploy("prune")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("agent-retired-1", self.gcp.state.images)
        self.assertIn("agent-android-older", self.gcp.state.images)

    def test_status_shows_the_familys_image(self):
        result = self.deploy("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agent-android-older (family agent-android)  commit abc123def456", result.stdout)


if __name__ == "__main__":
    unittest.main()
