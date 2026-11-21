"""images/cloud/orchestrator/supervisor.sh with a fake claude and fake GCP endpoints."""

import os
import re
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fake_gcp import FakeGcp
from support import REPO, base_env, write_executable

SUPERVISOR = REPO / "images/cloud/orchestrator/supervisor.sh"
PROJECT_ENV = "PROJECT_ID=lolay-agents\nPROJECT_NUMBER=613087327945\nREGION=us-central1\n"
JOBS = "projects/lolay-agents/locations/us-central1/jobs"

ORCHESTRATOR_ARGS = REPO / "images/cloud/orchestrator/orchestrator-args.sh"
CHECK_FLAGS = REPO / "scripts/cloud/check-orchestrator-flags.sh"

# Refuses what the real CLI would: another command, a flag the supervisor's one
# list doesn't have (the deploy checks that list against the real --help), or a
# flag with no value (r1.f10). Then records how it was started, logs a line on
# each stream, and drains on SIGTERM the way the orchestrator does.
FAKE_CLAUDE = f'source "{ORCHESTRATOR_ARGS}"\n' + r"""
if [[ "${1:-} ${2:-}" != "self-hosted-runner orchestrator" ]]; then
	echo "error: unknown command '$*'" >&2
	exit 1
fi
mapfile -t allowed < <(orchestrator_flags)
type="$RUNNER_TYPE"
trap 'echo term >>"$FAKE_OUT/$type.term.$$"; sleep "${FAKE_DRAIN:-0.3}"; exit 0' TERM
secret_file=""
args=("${@:3}")
for ((i = 0; i < ${#args[@]}; i += 2)); do
	flag="${args[i]}"
	if [[ " ${allowed[*]} " != *" $flag "* ]]; then
		echo "error: unknown option '$flag'" >&2
		exit 1
	fi
	if ((i + 1 >= ${#args[@]})) || [[ "${args[i + 1]}" == -* || -z "${args[i + 1]}" ]]; then
		echo "error: option '$flag' argument missing" >&2
		exit 1
	fi
	[[ "$flag" == --environment-secret-file ]] && secret_file="${args[i + 1]}"
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
        (self.env_dir / "project.env").write_text(PROJECT_ENV)
        (self.env_dir / "linux.env").write_text("MIN_IDLE=0\nEXPECTED_SPAWN_SECONDS=180\n")
        (self.env_dir / "gis.env").write_text("MIN_IDLE=1\n")
        self.secrets = self.tmp / "secrets"
        self.claude = write_executable(self.tmp / "bin/fake-claude", FAKE_CLAUDE)
        self.gcp = FakeGcp().__enter__()
        self.gcp.state.secrets.update({"claude-env-linux": "linux-key", "claude-env-gis": "gis-key"})
        self.add_job("linux")
        self.add_job("gis")
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

    def add_job(self, runner_type):
        """The type's Cloud Run job, as the deploy writes it."""
        name = f"{JOBS}/agent-{runner_type}"
        self.gcp.state.jobs[name] = {"name": name}

    def add_image(self, runner_type):
        """An image in the compute type's family, as vm-promote leaves it."""
        name = f"agent-{runner_type}-20261106-abc"
        self.gcp.state.images[name] = {"name": name, "family": f"agent-{runner_type}", "diskSizeGb": "100"}

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


    def test_a_type_with_deploy_false_gets_no_orchestrator(self):
        (self.env_dir / "linux-gpu.env").write_text("MIN_IDLE=0\nDEPLOY=false\n")
        self.gcp.state.secrets["claude-env-linux-gpu"] = "gpu-key"
        self.start()
        self.wait_for(lambda: self.starts("linux") and self.starts("gis"), what="both types")
        time.sleep(0.5)
        self.assertEqual(self.starts("linux-gpu"), [])
        self.assertIn("supervisor: linux-gpu: DEPLOY=false in its env file; not starting its orchestrator", self.lines)
        # It takes no health port: the deployed types keep theirs.
        self.assertIn("--health-port 18080", self.start_record("gis")["args"])
        self.assertIn("--health-port 18081", self.start_record("linux")["args"])

    def test_deploy_false_on_a_compute_type_is_skipped_not_fatal(self):
        # r3.f2: an env-file-only change reaches the host without deploy.sh's
        # refusal; exiting would restart-loop every type's orchestrator.
        (self.env_dir / "android.env").write_text("BACKEND=compute\nDEPLOY=false\n")
        self.gcp.state.secrets["claude-env-android"] = "android-key"
        self.start()
        self.wait_for(lambda: self.starts("linux") and self.starts("gis"), what="the other types")
        self.assertIsNone(self.process.poll())
        self.assertEqual(self.starts("android"), [])
        self.assertTrue(
            any(
                line.startswith("supervisor: android: DEPLOY=false is for Cloud Run types only")
                and line.endswith("; not starting its orchestrator")
                for line in self.lines
            ),
            self.lines,
        )
        self.stop()

    def test_only_deploy_false_types_is_an_error(self):
        for name in ("linux", "gis"):
            (self.env_dir / f"{name}.env").write_text("DEPLOY=false\n")
        self.start()
        self.assertEqual(self.process.wait(timeout=10), 1)
        self.wait_for(lambda: any(line.startswith("supervisor: no runner type left to start") for line in self.lines), what="the error")


class ReadinessTests(SupervisorCase):
    """A type starts once its job or image family exists (r1.f3)."""

    def test_a_type_with_no_job_waits_and_the_others_start(self):
        del self.gcp.state.jobs[f"{JOBS}/agent-gis"]
        self.start(RUNNER_WAIT_SEC="1")
        self.wait_for(lambda: self.starts("linux"), what="linux")
        waiting = ("supervisor: gis: no Cloud Run job agent-gis yet; not starting its orchestrator "
                   "until there is (checking every 1s)")
        self.wait_for(lambda: waiting in self.lines, what="the gis wait line")
        self.wait_for(lambda: len(self.gcp.requests("GET", "/jobs/agent-gis")) >= 3, what="gis checked again")
        self.assertEqual(self.starts("gis"), [])
        self.assertEqual(self.lines.count(waiting), 1, "logged once, not on every check")
        self.assertEqual(self.gcp.requests("GET", "/secrets/claude-env-gis/"), [], "no secret fetched for it")
        # It keeps its health port, so linux's doesn't move when it starts.
        self.assertIn("--health-port 18081", self.start_record("linux")["args"])
        self.add_job("gis")
        self.wait_for(lambda: self.starts("gis"), what="gis once its job exists")
        self.assertIn("supervisor: gis: its Cloud Run job agent-gis is there now", self.lines)
        self.assertIn("--health-port 18080", self.start_record("gis")["args"])
        self.stop()

    def test_a_compute_type_waits_for_an_image_in_its_family(self):
        (self.env_dir / "project.env").write_text(PROJECT_ENV + "ZONE=us-central1-a\n")
        (self.env_dir / "android.env").write_text("BACKEND=compute\n")
        self.gcp.state.secrets["claude-env-android"] = "android-key"
        # A job named for it is no use to a compute type.
        self.add_job("android")
        self.start(RUNNER_WAIT_SEC="1", REAPER_INTERVAL_SEC="3600")
        self.wait_for(lambda: self.starts("linux") and self.starts("gis"), what="the Cloud Run types")
        self.wait_for(
            lambda: "supervisor: android: no image in family agent-android yet; not starting its orchestrator "
                    "until there is (checking every 1s)" in self.lines,
            what="the android wait line",
        )
        self.assertEqual(self.starts("android"), [])
        self.add_image("android")
        self.wait_for(lambda: self.starts("android"), what="android once its family has an image")
        self.stop()

    def test_sigterm_during_a_slow_readiness_check_starts_nothing_more(self):
        # gis sorts first; its check takes 1.5 s, and the trap runs only once it
        # returns. Then neither gis nor linux may start: they'd miss the drain.
        self.gcp.state.delays["/jobs/agent-gis"] = 1.5
        self.start()
        self.wait_for(lambda: any(line.startswith("supervisor: runner types:") for line in self.lines),
                      what="the supervisor to start")
        time.sleep(0.5)
        self.assertEqual(self.stop(), 0)
        self.assertEqual(self.gcp.requests("GET", "/jobs/agent-linux"), [], "linux not even checked")
        self.assertEqual(self.gcp.requests("GET", "/secrets/"), [], "no secret fetched")
        self.assertFalse(any("started orchestrator" in line for line in self.lines), self.lines)
        tokens = self.gcp.requests("GET", "/service-accounts/default/token")
        self.assertEqual(len(tokens), 1)

    def test_one_token_serves_the_check_and_the_secret(self):
        self.start()
        self.wait_for(lambda: self.starts("linux") and self.starts("gis"), what="both types")
        self.assertEqual(len(self.gcp.requests("GET", "/service-accounts/default/token")), 2, "one per type")
        self.stop()

    def test_a_type_with_its_own_project_and_region_is_checked_there(self):
        # The hook runs gis's job where its env file says; project.env's place
        # has a job of that name too, which must not count.
        (self.env_dir / "gis.env").write_text("MIN_IDLE=1\nPROJECT_ID=other-project\nREGION=europe-west1\n")
        own = "/v2/projects/other-project/locations/europe-west1/jobs/agent-gis"
        self.start(RUNNER_WAIT_SEC="1")
        self.wait_for(lambda: self.starts("linux"), what="linux")
        self.wait_for(lambda: len(self.gcp.requests("GET", own)) >= 2, what="gis checked in its own region twice")
        self.assertEqual(self.starts("gis"), [])
        self.assertEqual(self.gcp.requests("GET", f"/v2/{JOBS}/agent-gis"), [], "not project.env's region")
        self.gcp.state.jobs[own.removeprefix("/v2/")] = {"name": own.removeprefix("/v2/")}
        self.wait_for(lambda: self.starts("gis"), what="gis once its job exists in its region")
        self.stop()

    def test_a_compute_type_with_its_own_project_is_checked_there(self):
        (self.env_dir / "project.env").write_text(PROJECT_ENV + "ZONE=us-central1-a\n")
        (self.env_dir / "android.env").write_text("BACKEND=compute\nPROJECT_ID=other-project\n")
        self.gcp.state.secrets["claude-env-android"] = "android-key"
        self.add_image("android")
        self.start(REAPER_INTERVAL_SEC="3600")
        self.wait_for(lambda: self.starts("android"), what="android")
        families = [request.path for request in self.gcp.requests("GET", "/images/family/")]
        self.assertEqual(families, ["/compute/v1/projects/other-project/global/images/family/agent-android"])
        self.stop()

    def test_an_answer_other_than_404_starts_it_anyway(self):
        self.gcp.fail("GET", "/jobs/agent-gis", 503, "unavailable")
        self.start(RUNNER_WAIT_SEC="3600")
        self.wait_for(lambda: self.starts("linux") and self.starts("gis"), what="both types")
        self.assertIn(
            "supervisor: gis: can't check for its Cloud Run job agent-gis (HTTP 503: unavailable); "
            "starting its orchestrator anyway",
            self.lines,
        )
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
            self.add_job(name)
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

    def test_a_process_that_cannot_be_read_is_skipped_and_logging_continues(self):
        # The race: the process is there for the readable check, then its
        # smaps_rollup fails to open (as /proc's does when the process exits). A
        # socket file passes -r and fails to open with ENXIO.
        proc_root = self.tmp / "proc"
        proc_root.mkdir()
        self.start(MEMORY_LOG_INTERVAL_SEC="1", AGENT_PROC_ROOT=str(proc_root))
        self.wait_for(lambda: self.starts("linux"), what="the orchestrator to start")
        pid = self.start_record("linux")["pid"]
        (proc_root / pid).mkdir()
        unreadable = proc_root / pid / "smaps_rollup"
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(unreadable))
            self.assertTrue(os.access(unreadable, os.R_OK))
            with self.assertRaises(OSError):
                open(unreadable)
            seen = len(self.lines)
            skipped = f"supervisor: memory type=linux pid={pid} processes=0 pss_kib=0 rss_kib=0 connected=unknown"
            self.wait_for(lambda: skipped in self.lines[seen:], what="the unreadable process to be skipped")
            seen = len(self.lines)
            self.wait_for(
                lambda: any(line.startswith("supervisor: memory total") for line in self.lines[seen:]),
                what="a total line after the unreadable process",
            )
        self.assertIsNone(self.process.poll(), "the supervisor must keep running")
        self.stop()


def vm(name, status="RUNNING", age_sec=60, work_order=True, type_="android"):
    created = datetime.now(timezone.utc) - timedelta(seconds=age_sec)
    items = [{"key": "runner-env", "value": "RUNNER_LABEL=x"}]
    if work_order:
        items.insert(0, {"key": "work-order", "value": "the-work-order"})
    return {
        "name": name,
        "status": status,
        "labels": {"managed-by": "agent-images", "agent-images-type": type_},
        "creationTimestamp": created.isoformat(timespec="milliseconds"),
        "metadata": {"items": items, "fingerprint": "fp-1"},
    }


class ReaperTests(SupervisorCase):
    def setUp(self):
        super().setUp()
        (self.env_dir / "project.env").write_text(PROJECT_ENV + "ZONE=us-central1-a\n")
        (self.env_dir / "android.env").write_text("BACKEND=compute\nMAX_RUN_DURATION_SEC=3600\n")
        self.gcp.state.secrets["claude-env-android"] = "android-key"
        self.add_image("android")

    def instances(self):
        return self.gcp.state.instances

    def test_deletes_stopped_and_overdue_vms_and_clears_read_orders(self):
        for instance in (
            vm("agent-android-done", status="TERMINATED"),
            vm("agent-android-overdue", age_sec=3600 + 400),
            vm("agent-android-busy"),
            vm("agent-android-new"),
            vm("agent-android-almost", age_sec=3600 + 100, work_order=False),
            vm("agent-gis-other", status="TERMINATED", type_="gis"),
            {**vm("agent-android-smoke-abc", status="TERMINATED"),
             "labels": {"managed-by": "agent-images", "agent-images-type": "android",
                        "agent-images-role": "smoke"}},
        ):
            self.instances()[instance["name"]] = instance
        self.gcp.state.instance_guest_attributes["agent-android-busy"] = {"agent-images/order": "read\n"}
        self.start(REAPER_INTERVAL_SEC="1")
        self.wait_for(
            lambda: "agent-android-done" not in self.instances()
            and "agent-android-overdue" not in self.instances()
            and self.instances()["agent-android-busy"]["metadata"]["fingerprint"] == "fp-2"
            # The supervisor logs the removal after the fake API answers, so the
            # state can change before the line is read.
            and any("removed the spent work order from agent-android-busy" in line for line in self.lines),
            what="a reaper pass",
        )
        busy_keys = [item["key"] for item in self.instances()["agent-android-busy"]["metadata"]["items"]]
        self.assertEqual(busy_keys, ["runner-env"])
        new_keys = [item["key"] for item in self.instances()["agent-android-new"]["metadata"]["items"]]
        self.assertIn("work-order", new_keys, "not read yet, so kept")
        self.assertIn("agent-android-almost", self.instances(), "within the grace")
        self.assertIn("agent-gis-other", self.instances(), "not a compute type")
        self.assertIn("agent-android-smoke-abc", self.instances(), "the deploy's to delete")
        log = "\n".join(self.lines)
        self.assertIn("supervisor: reaper: android: deleting agent-android-done (TERMINATED)", log)
        self.assertIn("reaper: android: deleting agent-android-overdue (RUNNING, ", log)
        self.assertIn("reaper: android: removed the spent work order from agent-android-busy", log)
        self.assertNotIn("the-work-order", log)
        self.stop()

    def test_runs_every_interval(self):
        self.start(REAPER_INTERVAL_SEC="1")
        self.wait_for(lambda: len(self.gcp.requests("GET", "/instances")) >= 2, what="two passes")
        listing = self.gcp.requests("GET", "/instances")[0]
        self.assertEqual(listing.query["filter"], "labels.agent-images-type=android")
        self.stop()

    def test_an_api_error_is_logged_and_retried(self):
        self.gcp.fail("GET", "/zones/us-central1-a/instances", 503, "unavailable")
        self.start(REAPER_INTERVAL_SEC="1")
        self.wait_for(
            lambda: sum("reaper: android: listing VMs: HTTP 503" in line for line in self.lines) >= 2,
            what="two failed passes",
        )
        self.assertTrue(self.starts("android") or self.starts("linux"))
        self.stop()

    def test_a_hung_api_does_not_hold_up_sigterm(self):
        self.gcp.state.delays["/zones/us-central1-a/instances"] = 4
        self.start(REAPER_INTERVAL_SEC="1", GCP_CURL_MAX_TIME="10")
        self.wait_for(lambda: self.gcp.requests("GET", "/instances") or len(self.lines) > 3, what="the reaper")
        self.wait_for(lambda: self.starts("android"), what="android")
        started = time.time()
        self.assertEqual(self.stop(), 0)
        self.assertLess(time.time() - started, 3)


class NoComputeTypesTests(SupervisorCase):
    def test_never_calls_compute_engine(self):
        self.start(REAPER_INTERVAL_SEC="1")
        self.wait_for(lambda: self.starts("linux") and self.starts("gis"), what="both types")
        time.sleep(1.5)
        self.assertEqual(self.gcp.requests(contains="/compute/"), [])
        self.stop()


def flags():
    listed = subprocess.run(
        ["bash", "-c", f'source "{ORCHESTRATOR_ARGS}"; orchestrator_flags'],
        capture_output=True, text=True, check=True,
    )
    return listed.stdout.split()


def help_text(flags_listed):
    """--help as the CLI prints it: an option line per flag, padded to the description."""
    lines = ["Usage: claude self-hosted-runner orchestrator [options]", "", "Options:"]
    lines += [f"  {flag} <value>".ljust(40) + "what it does" for flag in flags_listed]
    lines.append("  -h, --help".ljust(40) + "display help for command")
    return "\n".join(lines) + "\n"


# docker as the check runs it: `run --rm --entrypoint claude <image>
# self-hosted-runner orchestrator --help` and nothing else, printing $FAKE_HELP,
# or failing as a pull does when $FAKE_PULL_FAILS is set.
FAKE_DOCKER = r"""
expected="run --rm --entrypoint claude ${FAKE_IMAGE} self-hosted-runner orchestrator --help"
if [[ "$*" != "$expected" ]]; then
	echo "docker: fake: unexpected call: $*" >&2
	exit 125
fi
if [[ -n "${FAKE_PULL_FAILS:-}" ]]; then
	echo "docker: Error response from daemon: pull access denied for ${FAKE_IMAGE}" >&2
	exit 125
fi
cat "$FAKE_HELP"
"""

IMAGE = "registry.example/agent-orchestrator@sha256:abc"


class FlagCheckTests(unittest.TestCase):
    """scripts/cloud/check-orchestrator-flags.sh: the check of the supervisor's
    flags against an orchestrator image's own Claude (r1.f10)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=True)
        write_executable(self.tmp / "bin/docker", FAKE_DOCKER)

    def check(self, text, *args, **env):
        (self.tmp / "help.txt").write_text(text)
        return subprocess.run(
            ["bash", str(CHECK_FLAGS), *(args or (IMAGE,))], capture_output=True, text=True, timeout=30,
            env=base_env(self.tmp, self.tmp / "bin", FAKE_HELP=str(self.tmp / "help.txt"), FAKE_IMAGE=IMAGE, **env),
        )

    def test_the_supervisor_passes_the_flags_in_the_list(self):
        self.assertEqual(flags(), [
            "--hooks-dir", "--environment-secret-file", "--min-idle", "--expected-spawn-seconds",
            "--health-port", "--hook-timeout", "--shutdown-drain-sec",
        ])

    def test_passes_when_every_flag_is_listed(self):
        result = self.check(help_text(flags()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("all 7 flags the supervisor passes are listed", result.stdout)

    def test_fails_when_a_flag_is_missing(self):
        for missing in flags():
            with self.subTest(missing=missing):
                result = self.check(help_text([flag for flag in flags() if flag != missing]))
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"lists no {missing}, which the supervisor passes", result.stderr)

    def test_a_flag_named_only_in_a_description_is_missing(self):
        # A renamed flag's old name in the new one's description isn't the flag.
        others = [flag for flag in flags() if flag != "--environment-secret-file"]
        text = help_text(others) + "  --secret-file <path>".ljust(40) + "replaces --environment-secret-file\n"
        result = self.check(text)
        self.assertEqual(result.returncode, 1)
        self.assertIn("lists no --environment-secret-file", result.stderr)

    def test_fails_with_no_help_text(self):
        result = self.check("")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--help printed nothing", result.stderr)

    def test_an_image_that_wont_run_fails_with_dockers_error(self):
        # Not as seven missing flags (the help text was docker's error).
        result = self.check(help_text(flags()), FAKE_PULL_FAILS="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"couldn't run {IMAGE}'s claude self-hosted-runner orchestrator --help", result.stderr)
        self.assertIn("pull access denied", result.stderr)
        self.assertNotIn("lists no", result.stderr)

    def test_takes_exactly_one_image(self):
        result = self.check(help_text(flags()), IMAGE, "extra")
        self.assertEqual(result.returncode, 2)


class FlagCheckWiringTests(unittest.TestCase):
    """Where the workflows run the check: the deploy before the host takes the
    image, and the pull request check on the image it built (r1.f10)."""

    @staticmethod
    def step(job, name):
        text = job[job.index(f"      - name: {name}"):]
        return text[:text.find("\n      - ", 1)]

    def test_the_deploy_checks_before_pointing_the_host_at_the_image(self):
        workflow = (REPO / ".github/workflows/cloud-deploy.yml").read_text()
        job = workflow[workflow.index("\n  orchestrator:\n"):workflow.index("\n  prune:\n")]
        check = self.step(job, "Check the supervisor's flags")
        self.assertIn('scripts/cloud/check-orchestrator-flags.sh "$ARTIFACT_REGISTRY/agent-orchestrator@$digest"',
                      check)
        self.assertLess(job.index("check-orchestrator-flags.sh"), job.index("scripts/cloud/deploy.sh orchestrator"))
        self.assertLess(job.index("id: bake"), job.index("check-orchestrator-flags.sh"))

    def test_the_pull_request_checks_the_orchestrator_it_built(self):
        workflow = (REPO / ".github/workflows/cloud-build.yml").read_text()
        job = workflow[workflow.index("\n  build:\n"):workflow.index("\n  gate:\n")]
        check = self.step(job, "Check the supervisor's flags")
        self.assertIn("if: matrix.target == 'orchestrator'", check)
        # make cloud-build's local tag (REGISTRY empty, TAG local).
        self.assertIn("run: scripts/cloud/check-orchestrator-flags.sh agent-orchestrator:local", check)
        self.assertLess(job.index('make cloud-build TYPE="$TARGET"'), job.index("check-orchestrator-flags.sh"))
        bake = (REPO / "images/cloud/docker-bake.hcl").read_text()
        self.assertIn('result = [REGISTRY == "" ? "agent-${name}:${TAG}"', bake)
        self.assertRegex(bake, r'variable "TAG" \{\n  default = "local"\n\}')


class FakeClaudeTests(unittest.TestCase):
    """The fake refuses what the real CLI would, so a flag outside the list fails here."""

    def run_fake(self, *args):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(tmp)], check=True)
        claude = write_executable(tmp / "claude", FAKE_CLAUDE)
        return subprocess.run([str(claude), *args], capture_output=True, text=True, timeout=10,
                              env={"PATH": os.environ["PATH"], "RUNNER_TYPE": "linux", "FAKE_OUT": str(tmp)})

    def test_refuses_an_unknown_flag(self):
        result = self.run_fake("self-hosted-runner", "orchestrator", "--pool-secret-file", "/x")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown option '--pool-secret-file'", result.stderr)

    def test_refuses_a_flag_without_its_value(self):
        result = self.run_fake("self-hosted-runner", "orchestrator", "--min-idle", "--health-port", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("option '--min-idle' argument missing", result.stderr)

    def test_refuses_another_command(self):
        result = self.run_fake("self-hosted-runner", "runner")
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
