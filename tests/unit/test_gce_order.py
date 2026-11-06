"""images/android/gce/files/agent-gce-order.sh against the fake metadata server."""

import getpass
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from fake_gcp import FakeGcp
from support import REPO, base_env, read_env_file, write_executable

SCRIPT = REPO / "images/android/gce/files/agent-gce-order.sh"
WORK_ORDER = "eyJhbGciOi.work-order.signature"


class GceOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.poweroff = self.tmp / "powered-off"
        self.fake_bin = self.tmp / "bin"
        write_executable(self.fake_bin / "fake-poweroff", f'touch "{self.poweroff}"\n')
        self.gcp = FakeGcp().__enter__()

    def tearDown(self):
        self.gcp.__exit__(None, None, None)
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def run_order(self, **env):
        settings = {
            "GCP_METADATA_URL": self.gcp.env()["GCP_METADATA_URL"],
            "AGENT_GUEST_USER": getpass.getuser(),
            "AGENT_GUEST_HOME": str(self.home),
            "AGENT_POWEROFF_COMMAND": str(self.fake_bin / "fake-poweroff"),
            "AGENT_METADATA_RETRIES": "0",
        }
        settings.update(env)
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env=base_env(self.home, self.fake_bin, **settings),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotIn(WORK_ORDER, result.stdout + result.stderr)
        return result

    @property
    def runner_env(self):
        return self.home / ".config/agent-runner/runner.env"

    @property
    def secret(self):
        return self.home / ".claude-runner/environment-secret"

    def test_writes_the_order_and_runner_env_then_marks_it_read(self):
        self.gcp.state.attributes.update(
            {
                "work-order": WORK_ORDER,
                "runner-env": "RUNNER_LABEL=android-ord01234\nCLAUDE_RETIRE_AT=1767000000\n"
                "CLAUDE_BASE_DIR=/scratch/workspace\nCLAUDE_EXIT_IF_UNUSED_MIN=\n",
            }
        )
        result = self.run_order()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.poweroff.exists())
        self.assertEqual(self.secret.read_text(), WORK_ORDER)
        self.assertEqual(stat.S_IMODE(self.secret.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.secret.parent.stat().st_mode), 0o700)
        self.assertEqual(self.secret.stat().st_uid, os.getuid())
        values = read_env_file(self.runner_env)
        self.assertEqual(values["AGENT"], "claude")
        self.assertEqual(values["RUNNER_LABEL"], "android-ord01234")
        self.assertEqual(values["CLAUDE_RETIRE_AT"], "1767000000")
        self.assertEqual(values["CLAUDE_BASE_DIR"], "/scratch/workspace")
        self.assertEqual(values["CLAUDE_EXIT_IF_UNUSED_MIN"], "")
        self.assertEqual(values["EPHEMERAL"], "true")
        self.assertEqual(self.gcp.state.guest_attributes["agent-images/order"].strip(), "read")
        self.assertFalse((self.runner_env.parent / ".runner.env.tmp").exists())

    def test_drops_lines_that_are_not_plain_assignments(self):
        self.gcp.state.attributes.update(
            {
                "work-order": WORK_ORDER,
                "runner-env": "RUNNER_LABEL=ok\nFOO=$(touch /tmp/pwned)\nlower=x\nEPHEMERAL=false\n",
            }
        )
        result = self.run_order()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.runner_env.read_text()
        self.assertNotIn("pwned", text)
        self.assertNotIn("lower", text)
        self.assertIn("dropping", result.stderr)
        # EPHEMERAL=true comes last, so the hook can't turn it off.
        self.assertEqual(read_env_file(self.runner_env)["EPHEMERAL"], "true")

    def test_no_work_order_powers_off(self):
        result = self.run_order()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.poweroff.exists())
        self.assertFalse(self.runner_env.exists())
        self.assertFalse(self.secret.exists())

    def test_already_configured_does_nothing(self):
        self.runner_env.parent.mkdir(parents=True)
        self.runner_env.write_text("AGENT=claude\n")
        self.gcp.state.attributes["work-order"] = WORK_ORDER
        result = self.run_order()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.poweroff.exists())
        self.assertFalse(self.secret.exists())
        self.assertEqual(self.gcp.requests(), [])

    def test_debug_leaves_the_vm_up(self):
        self.gcp.state.attributes["agent-debug"] = "true"
        result = self.run_order()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.poweroff.exists())

    def test_smoke_reports_through_guest_attributes(self):
        self.gcp.state.attributes["agent-smoke"] = "true"
        smoke = write_executable(
            self.fake_bin / "fake-smoke",
            "echo 'ok    claude: 2.1.400'\necho emulator-boot-sec-cold=31.4\n"
            "echo emulator-boot-sec-warm=8.9\nexit 0\n",
        )
        result = self.run_order(AGENT_SMOKE_COMMAND=str(smoke))
        self.assertEqual(result.returncode, 0, result.stderr)
        attributes = {key: value.strip() for key, value in self.gcp.state.guest_attributes.items()}
        self.assertEqual(attributes["agent-images/smoke"], "pass")
        self.assertEqual(attributes["agent-images/emulator-boot-sec-cold"], "31.4")
        self.assertEqual(attributes["agent-images/emulator-boot-sec-warm"], "8.9")
        self.assertIn("claude: 2.1.400", attributes["agent-images/smoke-log"])
        self.assertTrue(self.poweroff.exists())
        # The result is written last: the deploy waits for it.
        puts = [r.path for r in self.gcp.requests("PUT")]
        self.assertTrue(puts[-1].endswith("/agent-images/smoke"))

    def test_failed_smoke_reports_fail(self):
        self.gcp.state.attributes["agent-smoke"] = "true"
        smoke = write_executable(self.fake_bin / "fake-smoke", "echo 'FAIL  kvm'\nexit 1\n")
        result = self.run_order(AGENT_SMOKE_COMMAND=str(smoke))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gcp.state.guest_attributes["agent-images/smoke"].strip(), "fail")
        self.assertTrue(self.poweroff.exists())

    def test_a_failure_powers_off(self):
        self.gcp.state.attributes["work-order"] = WORK_ORDER
        # A file where the config directory should be: install -d fails.
        (self.home / ".config").write_text("not a directory")
        result = self.run_order()
        self.assertTrue(self.poweroff.exists())
        self.assertIn("failed", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
