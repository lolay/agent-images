"""images/cloud/entrypoint.sh, run outside a container with fake commands."""

import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from support import REPO, base_env, read_env_file, write_executable

ENTRYPOINT = REPO / "images/cloud/entrypoint.sh"
PROFILE = REPO / "images/cloud/base/profile.sh"


class EntrypointCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.scratch = self.tmp / "scratch"
        self.datasets = self.tmp / "datasets"
        self.outputs = self.tmp / "outputs"
        self.out = self.tmp / "out"
        self.fake_bin = self.tmp / "bin"
        for path in (self.home, self.scratch, self.datasets, self.outputs, self.out):
            path.mkdir()
        # The runner the entrypoint execs: record what it was given.
        self.runner = write_executable(
            self.fake_bin / "fake-runner",
            'env >"$FAKE_OUT/env"\n'
            'cp "$HOME/.config/agent-runner/runner.env" "$FAKE_OUT/runner.env"\n'
            'sleep "${FAKE_RUNNER_SLEEP:-0}"\n',
        )
        self.watchdog = write_executable(
            self.fake_bin / "fake-watchdog", 'echo ran >>"$FAKE_OUT/watchdog"\n'
        )
        write_executable(self.fake_bin / "claude", "echo '9.9.9 (Claude Code)'\n")
        self.brew_prefix = self.tmp / "linuxbrew"
        for sub in ("Cellar", "bin"):
            (self.brew_prefix / sub).mkdir(parents=True)
        write_executable(
            self.fake_bin / "brew",
            'if [[ "$1" == --prefix ]]; then echo "$FAKE_BREW_PREFIX"; else echo "Homebrew 9.9"; fi\n',
        )

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def run_entrypoint(self, *args, **env):
        settings = {
            "AGENT_SCRATCH": str(self.scratch),
            "AGENT_DATASETS": str(self.datasets),
            "AGENT_OUTPUTS_ROOT": str(self.outputs),
            "AGENT_PROFILE": str(PROFILE),
            "AGENT_RUNNER_COMMAND": str(self.runner),
            "AGENT_WATCHDOG_COMMAND": str(self.watchdog),
            # The loop outlives the runner by at most one interval, holding the pipe.
            "AGENT_WATCHDOG_INTERVAL_SEC": "0.1",
            "AGENT_REQUIRE_SCRATCH_MOUNT": "false",
            "AGENT_TYPE": "linux",
            "FAKE_OUT": str(self.out),
            "FAKE_BREW_PREFIX": str(self.brew_prefix),
            "SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET": "work-order-jwt",
            "RUNNER_LABEL": "linux-abc123",
        }
        settings.update(env)
        settings = {key: value for key, value in settings.items() if value is not None}
        return subprocess.run(
            ["bash", str(ENTRYPOINT), *args],
            env=base_env(self.home, self.fake_bin, **settings),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def runner_env(self) -> dict:
        return dict(
            line.split("=", 1)
            for line in (self.out / "env").read_text().splitlines()
            if "=" in line
        )


class RunTests(EntrypointCase):
    def test_points_temp_caches_and_checkouts_at_scratch(self):
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self.runner_env()
        self.assertEqual(env["TMPDIR"], f"{self.scratch}/tmp")
        self.assertEqual(env["XDG_CACHE_HOME"], f"{self.scratch}/cache")
        self.assertEqual(env["PIP_CACHE_DIR"], f"{self.scratch}/cache/pip")
        self.assertEqual(env["HOMEBREW_CACHE"], f"{self.scratch}/cache/Homebrew")
        self.assertEqual(env["CLAUDE_BASE_DIR"], f"{self.scratch}/workspace")
        self.assertEqual(env["AGENT_SCRATCH"], str(self.scratch))
        self.assertEqual(env["AGENT_DATASETS"], str(self.datasets))
        for sub in ("workspace", "tmp", "cache"):
            self.assertTrue((self.scratch / sub).is_dir(), sub)

    def test_moves_the_work_order_to_an_owner_only_file(self):
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET", self.runner_env())
        secret = self.home / ".claude-runner/environment-secret"
        self.assertEqual(secret.read_text(), "work-order-jwt")
        self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)
        self.assertNotIn("work-order-jwt", result.stdout + result.stderr)
        self.assertNotIn("work-order-jwt", (self.out / "runner.env").read_text())

    def test_writes_runner_env_for_agent_runner(self):
        result = self.run_entrypoint(
            CLAUDE_RELEASE_IDLE_SESSION_MIN="60",
            CLAUDE_KILL_SESSION_AFTER_MIN="480",
            CLAUDE_EXIT_IF_UNUSED_MIN="60",
            CLAUDE_UPGRADE="true",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        runner_env = read_env_file(self.out / "runner.env")
        self.assertEqual(runner_env["AGENT"], "claude")
        self.assertEqual(runner_env["RUNNER_LABEL"], "linux-abc123")
        self.assertEqual(runner_env["CLAUDE_BASE_DIR"], f"{self.scratch}/workspace")
        self.assertEqual(runner_env["CLAUDE_RELEASE_IDLE_SESSION_MIN"], "60")
        self.assertEqual(runner_env["CLAUDE_KILL_SESSION_AFTER_MIN"], "480")
        self.assertEqual(runner_env["CLAUDE_EXIT_IF_UNUSED_MIN"], "60")
        self.assertEqual(runner_env["CLAUDE_UPGRADE"], "true")
        self.assertNotIn("CLAUDE_RETIRE_AT", runner_env)
        mode = stat.S_IMODE((self.home / ".config/agent-runner/runner.env").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_retires_the_margin_before_the_task_timeout(self):
        before = int(time.time())
        result = self.run_entrypoint(AGENT_TASK_TIMEOUT_SEC="34200", AGENT_RETIRE_MARGIN_SEC="900")
        after = int(time.time())
        self.assertEqual(result.returncode, 0, result.stderr)
        retire_at = int(read_env_file(self.out / "runner.env")["CLAUDE_RETIRE_AT"])
        self.assertGreaterEqual(retire_at, before + 34200 - 900)
        self.assertLessEqual(retire_at, after + 34200 - 900)
        self.assertEqual(self.runner_env()["AGENT_RETIRE_AT"], str(retire_at))

    def test_label_defaults_to_type_and_execution(self):
        result = self.run_entrypoint(RUNNER_LABEL=None, CLOUD_RUN_EXECUTION="agent-linux-x7k2p")
        self.assertEqual(result.returncode, 0, result.stderr)
        runner_env = read_env_file(self.out / "runner.env")
        self.assertEqual(runner_env["RUNNER_LABEL"], "linux-agent-linux-x7k2p")

    def test_runs_the_watchdog_beside_the_runner(self):
        result = self.run_entrypoint(FAKE_RUNNER_SLEEP="0.6")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(len((self.out / "watchdog").read_text().splitlines()), 2)


class RefusalTests(EntrypointCase):
    def test_refuses_without_a_work_order(self):
        result = self.run_entrypoint(SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET=None)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no work order", result.stderr)
        self.assertFalse((self.out / "env").exists())

    def test_refuses_when_scratch_is_not_a_mount(self):
        result = self.run_entrypoint(AGENT_REQUIRE_SCRATCH_MOUNT="true")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a mount", result.stderr)
        self.assertFalse((self.out / "env").exists())

    def test_refuses_a_timeout_inside_the_margin(self):
        result = self.run_entrypoint(AGENT_TASK_TIMEOUT_SEC="600", AGENT_RETIRE_MARGIN_SEC="900")
        self.assertEqual(result.returncode, 1)
        self.assertIn("margin", result.stderr)


class SmokeTests(EntrypointCase):
    def test_reports_every_check_and_fails_on_any(self):
        # A temp dir is no mount, and root (or a writable dir) can write datasets.
        result = self.run_entrypoint("smoke", SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET=None)
        self.assertEqual(result.returncode, 1)
        lines = [line for line in result.stdout.splitlines() if line.startswith("smoke: ")]
        checks = {line.split()[2]: line.split()[1] for line in lines}
        self.assertEqual(
            set(checks), {"claude", "scratch", "outputs", "datasets", "homebrew"}
        )
        self.assertEqual(checks["claude"], "pass")
        self.assertEqual(checks["outputs"], "pass")
        self.assertEqual(checks["homebrew"], "pass")
        self.assertEqual(checks["scratch"], "FAIL")
        self.assertEqual(checks["datasets"], "FAIL")
        self.assertEqual(list((self.outputs / "_smoke").iterdir()), [])
        self.assertFalse((self.out / "env").exists(), "smoke must not start the runner")

    def test_homebrew_must_be_writable_by_the_session_user(self):
        result = self.run_entrypoint("smoke", FAKE_BREW_PREFIX=str(self.tmp / "missing"))
        line = next(line for line in result.stdout.splitlines() if " homebrew " in line)
        self.assertTrue(line.startswith("smoke: FAIL homebrew"), line)
        self.assertIn("isn't writable", line)


if __name__ == "__main__":
    unittest.main()
