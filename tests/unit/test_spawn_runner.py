"""hooks/gcp/spawn-runner against the fake GCP endpoints."""

import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from fake_gcp import FakeGcp
from support import REPO, base_env

HOOK = REPO / "hooks/gcp/spawn-runner"
WORK_ORDER = "eyJhbGciOi.work-order.signature"
ORDER_ID = "ORD0123456789abcdef"
RUN_PATH = "/v2/projects/lolay-agents/locations/us-central1/jobs/agent-linux:run"
INSTANCES_PATH = "/compute/v1/projects/lolay-agents/zones/us-central1-a/instances"
INSTANCE = "agent-android-ord0123456789abcdef"


class SpawnRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.work_order = self.tmp / "work-order"
        self.work_order.write_text(WORK_ORDER + "\n")
        self.gcp = FakeGcp().__enter__()

    def tearDown(self):
        self.gcp.__exit__(None, None, None)
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def run_hook(self, **env):
        settings = {
            **self.gcp.env(),
            "RUNNER_TYPE": "linux",
            "CLAUDE_RUNNER_WORK_ORDER_FILE": str(self.work_order),
            "CLAUDE_RUNNER_ORDER_ID": ORDER_ID,
            "CLAUDE_RUNNER_SESSION_ID": "session_01XYZ",
            "SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET": "the-environment-key",
        }
        settings.update(env)
        settings = {key: value for key, value in settings.items() if value is not None}
        result = subprocess.run(
            ["bash", str(HOOK)],
            env=base_env(self.tmp, self.tmp / "no-bin", **settings),
            capture_output=True,
            text=True,
            timeout=30,
        )
        for secret in (WORK_ORDER, "the-environment-key", "fake-token-1234"):
            self.assertNotIn(secret, result.stdout + result.stderr)
        return result

    def marker_key(self):
        return ("lolay-agents-orders", f"orders/{ORDER_ID}")

    def test_creates_the_marker_then_runs_the_job(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.marker_key(), self.gcp.state.objects)
        upload = self.gcp.requests("POST", "/upload/storage/v1/b/lolay-agents-orders/o")[0]
        self.assertEqual(upload.query["ifGenerationMatch"], "0")
        self.assertEqual(upload.json(), {"type": "linux", "session": "session_01XYZ"})
        runs = self.gcp.requests("POST", ":run")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].path, RUN_PATH)
        override = runs[0].json()["overrides"]["containerOverrides"][0]
        self.assertEqual(override["name"], "runner")
        env = {item["name"]: item["value"] for item in override["env"]}
        self.assertEqual(
            env,
            {"SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET": WORK_ORDER, "RUNNER_LABEL": "linux-ord01234"},
        )
        self.assertIn("started agent-linux-abc12", result.stdout)

    def test_the_work_order_is_only_in_the_run_body(self):
        self.run_hook()
        for request in self.gcp.state.requests:
            if request.path == RUN_PATH:
                continue
            self.assertNotIn(WORK_ORDER.encode(), request.body + request.path.encode())
            self.assertNotIn("the-environment-key".encode(), request.body)

    def test_a_redelivered_order_runs_nothing(self):
        self.gcp.state.objects[self.marker_key()] = b"{}"
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already has an execution", result.stdout)
        self.assertEqual(self.gcp.requests("POST", ":run"), [])
        self.assertIn(self.marker_key(), self.gcp.state.objects, "kept for the first one")

    def test_a_failed_run_deletes_the_marker_and_retries(self):
        self.gcp.fail("POST", ":run", 500, "backend unavailable")
        result = self.run_hook()
        self.assertEqual(result.returncode, 1)
        self.assertIn("running agent-linux: HTTP 500: backend unavailable", result.stderr)
        self.assertNotIn(self.marker_key(), self.gcp.state.objects)
        self.assertEqual(len(self.gcp.requests("DELETE", f"orders/{ORDER_ID}")), 1)

    def test_a_missing_job_does_not_retry(self):
        self.gcp.fail("POST", ":run", 404, "Resource 'agent-linux' was not found")
        result = self.run_hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn("HTTP 404", result.stderr)
        self.assertNotIn(self.marker_key(), self.gcp.state.objects)

    def test_no_permission_on_the_bucket_does_not_retry(self):
        self.gcp.fail("POST", "/upload/storage/v1/b/lolay-agents-orders/o", 403, "denied")
        result = self.run_hook()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.gcp.requests("POST", ":run"), [])

    def test_no_token_retries(self):
        self.gcp.fail("GET", "/service-accounts/default/token", 500)
        result = self.run_hook()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no access token", result.stderr)
        self.assertEqual(self.gcp.requests("POST"), [])

    def test_unreachable_api_retries(self):
        result = self.run_hook(GCP_RUN_URL="http://127.0.0.1:9")
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 000", result.stderr)
        self.assertNotIn(self.marker_key(), self.gcp.state.objects)

    def test_an_unknown_type_does_not_retry(self):
        result = self.run_hook(RUNNER_TYPE="nosuchtype")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.gcp.state.requests, [])

    def test_an_empty_work_order_retries(self):
        self.work_order.write_text("")
        result = self.run_hook()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.gcp.state.requests, [])


class SpawnComputeTests(SpawnRunnerTests):
    """The compute backend (infra/gcp/android.env): a Compute Engine VM per order."""

    def run_hook(self, **env):
        return super().run_hook(**{"RUNNER_TYPE": "android", "GCP_POLL_SEC": "0.1", **env})

    def insert(self):
        inserts = self.gcp.requests("POST", INSTANCES_PATH)
        self.assertEqual(len(inserts), 1)
        return inserts[0].json()

    # The Cloud Run tests in the parent don't apply here; these replace them.
    def test_creates_the_marker_then_runs_the_job(self):
        started = int(time.time())
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.marker_key(), self.gcp.state.objects)
        upload = self.gcp.requests("POST", "/upload/storage/v1/b/lolay-agents-orders/o")[0]
        self.assertEqual(upload.json(), {"type": "android", "session": "session_01XYZ"})
        self.assertEqual(self.gcp.requests("POST", ":run"), [])
        body = self.insert()
        self.assertEqual(body["name"], INSTANCE)
        self.assertEqual(body["machineType"], "zones/us-central1-a/machineTypes/n2-standard-8")
        self.assertEqual(
            body["labels"],
            {"managed-by": "agent-images", "agent-images-type": "android",
             "agent-images-order": "ord0123456789abcdef"},
        )
        disk = body["disks"][0]
        self.assertTrue(disk["boot"] and disk["autoDelete"])
        self.assertEqual(
            disk["initializeParams"],
            {"sourceImage": "projects/lolay-agents/global/images/family/agent-android",
             "diskSizeGb": "100", "diskType": "zones/us-central1-a/diskTypes/pd-balanced"},
        )
        nic = body["networkInterfaces"][0]
        self.assertNotIn("accessConfigs", nic, "no external IP")
        self.assertEqual(nic["subnetwork"], "projects/lolay-agents/regions/us-central1/subnetworks/agents-us-central1")
        self.assertEqual(body["serviceAccounts"][0]["email"], "agent-runner@lolay-agents.iam.gserviceaccount.com")
        self.assertEqual(body["advancedMachineFeatures"], {"enableNestedVirtualization": True})
        self.assertEqual(body["scheduling"]["maxRunDuration"], {"seconds": "34200"})
        self.assertEqual(body["scheduling"]["instanceTerminationAction"], "DELETE")
        metadata = {item["key"]: item["value"] for item in body["metadata"]["items"]}
        self.assertEqual(metadata["work-order"], WORK_ORDER)
        self.assertEqual(metadata["enable-guest-attributes"], "TRUE")
        runner_env = dict(line.split("=", 1) for line in metadata["runner-env"].splitlines())
        self.assertEqual(runner_env["RUNNER_LABEL"], "android-ord01234")
        self.assertEqual(runner_env["CLAUDE_BASE_DIR"], "/scratch/workspace")
        self.assertEqual(runner_env["CLAUDE_KILL_SESSION_AFTER_MIN"], "480")
        retire_at = int(runner_env["CLAUDE_RETIRE_AT"])
        self.assertLessEqual(abs(retire_at - (started + 34200 - 900)), 5)
        self.assertIn(f"created VM {INSTANCE}", result.stdout)

    def test_the_work_order_is_only_in_the_run_body(self):
        self.run_hook()
        for request in self.gcp.state.requests:
            if request.method == "POST" and request.path == INSTANCES_PATH:
                continue
            self.assertNotIn(WORK_ORDER.encode(), request.body + request.path.encode())
            self.assertNotIn("the-environment-key".encode(), request.body)

    def test_a_redelivered_order_runs_nothing(self):
        self.gcp.state.objects[self.marker_key()] = b"{}"
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gcp.requests("POST", INSTANCES_PATH), [])

    def test_a_failed_run_deletes_the_marker_and_retries(self):
        self.gcp.fail("POST", INSTANCES_PATH, 503, "backend unavailable")
        result = self.run_hook()
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 503: backend unavailable", result.stderr)
        self.assertNotIn(self.marker_key(), self.gcp.state.objects)

    def test_a_missing_job_does_not_retry(self):
        self.gcp.state.operation_errors[INSTANCE] = "RESOURCE_NOT_FOUND"
        result = self.run_hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn("RESOURCE_NOT_FOUND", result.stderr)
        self.assertNotIn(self.marker_key(), self.gcp.state.objects)

    def test_unreachable_api_retries(self):
        result = self.run_hook(GCP_COMPUTE_URL="http://127.0.0.1:9")
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 000", result.stderr)
        self.assertNotIn(self.marker_key(), self.gcp.state.objects)

    def test_an_existing_vm_for_the_order_is_success(self):
        self.gcp.state.instances[INSTANCE] = {"name": INSTANCE, "metadata": {}}
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already has VM", result.stdout)
        self.assertIn(self.marker_key(), self.gcp.state.objects)

    def test_quota_and_capacity_retry(self):
        for code in ("QUOTA_EXCEEDED", "ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS"):
            with self.subTest(code=code):
                self.gcp.state.operation_errors[INSTANCE] = code
                self.gcp.state.objects.clear()
                result = self.run_hook()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(code, result.stderr)
                self.assertNotIn(self.marker_key(), self.gcp.state.objects)

    def test_a_quota_refusal_on_the_request_retries(self):
        self.gcp.state.failures[("POST", INSTANCES_PATH)] = (
            403, {"error": {"message": "Quota 'N2_CPUS' exceeded.", "errors": [{"reason": "quotaExceeded"}]}})
        result = self.run_hook()
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_no_permission_does_not_retry(self):
        self.gcp.fail("POST", INSTANCES_PATH, 403, "Required 'compute.instances.create' permission")
        result = self.run_hook()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(self.marker_key(), self.gcp.state.objects)

    def test_a_slow_insert_is_left_to_finish(self):
        self.gcp.state.pending_operations = True
        result = self.run_hook(INSTANCE_INSERT_WAIT_SEC="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("still being created", result.stdout)
        self.assertIn(self.marker_key(), self.gcp.state.objects)

    def test_an_unknown_backend_does_not_retry(self):
        env_dir = self.tmp / "infra"
        env_dir.mkdir()
        shutil.copy(REPO / "infra/gcp/project.env", env_dir)
        (env_dir / "android.env").write_text("BACKEND=lambda\n")
        result = self.run_hook(AGENT_GCP_ENV_DIR=str(env_dir))
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown BACKEND=lambda", result.stderr)
        self.assertEqual(self.gcp.state.requests, [])


if __name__ == "__main__":
    unittest.main()
