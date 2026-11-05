"""images/cloud/orchestrator/supervisor.sh with a fake claude and fake GCP endpoints."""

import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fake_gcp import FakeGcp
from support import REPO, base_env, write_executable

SUPERVISOR = REPO / "images/cloud/orchestrator/supervisor.sh"

# Records how it was started, logs a line on each stream, and drains on SIGTERM
# the way the orchestrator does.
FAKE_CLAUDE = r"""
type="$RUNNER_TYPE"
trap 'echo term >>"$FAKE_OUT/$type.term.$$"; sleep "${FAKE_DRAIN:-0.3}"; exit 0' TERM
secret_file=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
	[[ "${args[i]}" == --environment-secret-file ]] && secret_file="${args[i + 1]}"
done
# Written whole, then moved into place: the test reads it as soon as it exists.
{
	echo "pid=$$"
	echo "args=$*"
	echo "secret=$(cat "$secret_file")"
} >"$FAKE_OUT/.$type.start.$$"
mv "$FAKE_OUT/.$type.start.$$" "$FAKE_OUT/$type.start.$$"
echo "hello from $type"
echo "a warning from $type" >&2
mode_var="FAKE_MODE_${type//-/_}"
if [[ "${!mode_var:-serve}" == exit ]]; then
	exit 3
fi
while :; do sleep 0.1; done
"""


class SupervisorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.env_dir = self.tmp / "infra"
        self.env_dir.mkdir()
        (self.env_dir / "project.env").write_text("PROJECT_ID=lolay-agents\nPROJECT_NUMBER=613087327945\n")
        (self.env_dir / "linux.env").write_text("MIN_IDLE=0\nEXPECTED_SPAWN_SECONDS=180\n")
        (self.env_dir / "gis.env").write_text("MIN_IDLE=1\n")
        self.secrets = self.tmp / "secrets"
        self.claude = write_executable(self.tmp / "bin/fake-claude", FAKE_CLAUDE)
        self.gcp = FakeGcp().__enter__()
        self.gcp.state.secrets.update({"claude-env-linux": "linux-key", "claude-env-gis": "gis-key"})
        self.lines = []
        self.process = None
        self.reader = None

    def tearDown(self):
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        # Its children hold the log pipe: end them first, so the reader sees EOF
        # and closing the pipe can't block on the reader's lock.
        subprocess.run(["pkill", "-f", str(self.claude)], check=False)
        if self.reader:
            self.reader.join(timeout=5)
        if self.process:
            self.process.stdout.close()
        self.gcp.__exit__(None, None, None)
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def start(self, **env):
        settings = {
            **self.gcp.env(),
            "AGENT_ORCHESTRATOR_ROOT": str(REPO),
            "AGENT_GCP_ENV_DIR": str(self.env_dir),
            "SUPERVISOR_SECRETS_DIR": str(self.secrets),
            "CLAUDE_BIN": str(self.claude),
            "HEALTH_PORT_BASE": "18080",
            "TICK_SEC": "0.2",
            "MEMORY_LOG_INTERVAL_SEC": "3600",
            "RESTART_MIN_SEC": "1",
            "SECRET_RETRY_SEC": "1",
            "FAKE_OUT": str(self.out),
        }
        settings.update(env)
        self.process = subprocess.Popen(
            ["bash", str(SUPERVISOR)],
            env=base_env(self.tmp, self.tmp / "bin", **settings),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.reader = threading.Thread(target=self.collect, daemon=True)
        self.reader.start()

    def collect(self):
        for line in self.process.stdout:
            self.lines.append(line.rstrip("\n"))

    def wait_for(self, condition, timeout=10, what="condition"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if condition():
                return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {what}; output:\n" + "\n".join(self.lines))

    def starts(self, runner_type):
        return sorted(self.out.glob(f"{runner_type}.start.*"))

    def start_record(self, runner_type, index=0):
        text = self.starts(runner_type)[index].read_text()
        return dict(line.split("=", 1) for line in text.splitlines())

    def stop(self, timeout=10):
        self.process.send_signal(signal.SIGTERM)
        return self.process.wait(timeout=timeout)


class StartTests(SupervisorCase):
    def test_one_orchestrator_per_type_with_its_own_secret(self):
        self.start()
        self.wait_for(lambda: self.starts("linux") and self.starts("gis"), what="both types")
        gis, linux = self.start_record("gis"), self.start_record("linux")
        self.assertEqual(linux["secret"], "linux-key")
        self.assertEqual(gis["secret"], "gis-key")
        # Types sort by file name: gis gets the first port.
        self.assertIn("--health-port 18080", gis["args"])
        self.assertIn("--health-port 18081", linux["args"])
        self.assertIn("--min-idle 1", gis["args"])
        self.assertIn("--min-idle 0 --expected-spawn-seconds 180", linux["args"])
        self.assertTrue(linux["args"].startswith("self-hosted-runner orchestrator --hooks-dir "))
        self.assertIn(f"--hooks-dir {REPO}/hooks/gcp", linux["args"])
        self.assertIn("--hook-timeout 60 --shutdown-drain-sec 70", linux["args"])
        self.assertIn(f"--environment-secret-file {self.secrets}/linux", linux["args"])
        self.assertEqual(stat.S_IMODE((self.secrets / "linux").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.secrets.stat().st_mode), 0o700)
        self.assertNotIn("project", {path.name.split(".")[0] for path in self.out.iterdir()})
        self.stop()

    def test_prefixes_each_orchestrators_lines_with_its_type(self):
        self.start()
        self.wait_for(lambda: "[linux] hello from linux" in self.lines, what="linux's stdout")
        self.wait_for(lambda: "[linux] a warning from linux" in self.lines, what="linux's stderr")
        self.wait_for(lambda: "[gis] hello from gis" in self.lines, what="gis's stdout")
        self.stop()

    def test_secrets_never_reach_the_log(self):
        self.start()
        self.wait_for(lambda: self.starts("linux") and self.starts("gis"), what="both types")
        self.stop()
        output = "\n".join(self.lines)
        self.assertNotIn("linux-key", output)
        self.assertNotIn("gis-key", output)

    def test_a_missing_secret_does_not_hold_up_the_others(self):
        del self.gcp.state.secrets["claude-env-gis"]
        self.start()
        self.wait_for(lambda: self.starts("linux"), what="linux")
        self.wait_for(
            lambda: any("gis: can't read secret claude-env-gis: HTTP 404" in line for line in self.lines),
            what="the gis secret error",
        )
        self.assertEqual(self.starts("gis"), [])
        self.gcp.state.secrets["claude-env-gis"] = "gis-key"
        self.wait_for(lambda: self.starts("gis"), what="gis after its secret appears")
        self.stop()


class SupervisionTests(SupervisorCase):
    def test_restarts_an_orchestrator_that_exits(self):
        self.start(FAKE_MODE_gis="exit")
        self.wait_for(lambda: len(self.starts("gis")) >= 2, timeout=15, what="a gis restart")
        self.assertEqual(len(self.starts("linux")), 1)
        self.assertTrue(any("gis: orchestrator exited (3)" in line for line in self.lines))
        self.stop()

    def test_sigterm_reaches_every_orchestrator_and_waits_for_them(self):
        self.start(FAKE_DRAIN="1")
        self.wait_for(lambda: self.starts("linux") and self.starts("gis"), what="both types")
        began = time.time()
        status = self.stop()
        elapsed = time.time() - began
        self.assertEqual(status, 0)
        self.assertEqual(len(list(self.out.glob("linux.term.*"))), 1)
        self.assertEqual(len(list(self.out.glob("gis.term.*"))), 1)
        # Both drained at once, not one after the other.
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 1.9)
        self.assertIn("supervisor: every orchestrator has stopped", self.lines)
        alive = subprocess.run(["pgrep", "-f", str(self.claude)], capture_output=True)
        self.assertNotEqual(alive.returncode, 0, "no orchestrator outlives the supervisor")

    def test_orchestrators_ending_together_are_all_collected(self):
        # Several children exiting at once must all be collected, or the
        # supervisor never exits.
        for name in ("alpha", "beta", "gamma"):
            (self.env_dir / f"{name}.env").write_text("MIN_IDLE=0\n")
            self.gcp.state.secrets[f"claude-env-{name}"] = f"{name}-key"
        self.start(FAKE_DRAIN="0")
        types = ("alpha", "beta", "gamma", "gis", "linux")
        self.wait_for(lambda: all(self.starts(name) for name in types), what="five types")
        self.assertEqual(self.stop(), 0)

    def test_sigterm_during_a_start_still_reaches_that_orchestrator(self):
        # linux's secret takes a second, so the signal lands while it starts.
        self.gcp.state.delays["/secrets/claude-env-linux/versions/latest:access"] = 1
        self.start()
        self.wait_for(lambda: self.starts("gis"), what="gis")
        self.assertEqual(self.starts("linux"), [])
        status = self.stop()
        self.assertEqual(status, 0)
        alive = subprocess.run(["pgrep", "-f", str(self.claude)], capture_output=True)
        self.assertNotEqual(alive.returncode, 0, "linux started late, and was stopped too")


class MemoryTests(SupervisorCase):
    def test_logs_each_orchestrators_memory_and_a_total(self):
        self.start(MEMORY_LOG_INTERVAL_SEC="1")
        pattern = re.compile(
            r"^supervisor: memory type=linux pid=(\d+) processes=(\d+) pss_kib=(\d+) "
            r"rss_kib=(\d+) connected=unknown$"
        )
        self.wait_for(lambda: any(pattern.match(line) for line in self.lines), what="a memory line")
        match = next(pattern.match(line) for line in self.lines if pattern.match(line))
        pid, processes, pss, rss = (int(group) for group in match.groups())
        # The pid is the orchestrator itself, not a wrapper around it.
        self.assertEqual(pid, int(self.start_record("linux")["pid"]))
        self.assertGreaterEqual(processes, 2, "the orchestrator and its log prefixer at least")
        self.assertGreater(pss, 0)
        self.assertGreaterEqual(rss, pss)
        self.wait_for(
            lambda: any(
                re.match(
                    r"^supervisor: memory total orchestrators_pss_kib=\d+ supervisor_pss_kib=\d+ "
                    r"mem_available_kib=\d+ mem_total_kib=\d+$",
                    line,
                )
                for line in self.lines
            ),
            what="a total line",
        )
        self.stop()


if __name__ == "__main__":
    unittest.main()
