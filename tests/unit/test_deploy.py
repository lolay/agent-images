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
        job("agent-gis", {**ours, "agent-images-type": "gis"})
        job("agent-gis-next", {**ours, "agent-images-type": "gis"})
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


if __name__ == "__main__":
    unittest.main()
