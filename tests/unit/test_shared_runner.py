"""The shared runner scripts every platform runs, with fake commands: claude.sh
(the upgrade and the runner.env -> CLI flag mapping), agent-runner.sh (EPHEMERAL
power-off) and the watchdog (its repair and second-failure rule). Then the two GCP chains
end to end: job spec -> entrypoint -> claude.sh, and the compute hook -> instance
metadata -> agent-gce-order -> claude.sh."""

import getpass
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from fake_gcp import FakeGcp
from support import REPO, base_env, git_env, read_env_file, write_executable
from test_entrypoint import EntrypointCase

CLAUDE_SH = REPO / "images/shared/runners/claude.sh"
AGENT_RUNNER = REPO / "images/shared/agent-runner.sh"
WATCHDOG = REPO / "images/shared/agent-runner-watchdog.sh"
JOB_SPEC = REPO / "scripts/cloud/job-spec.sh"
HOOK = REPO / "hooks/gcp/spawn-runner"
GCE_ORDER = REPO / "images/android/gce/files/agent-gce-order.sh"
IMAGE = "us-central1-docker.pkg.dev/lolay-agents/agent-images/agent-linux@sha256:" + "a" * 64
RUNNER_KEYS = ("EXIT_IF_UNUSED_MIN", "RELEASE_IDLE_SESSION_MIN", "KILL_SESSION_AFTER_MIN")

# A fake claude: each call's argv goes to $FAKE_OUT/claude.calls, one per line
# (fields separated by a tab), and the runner's argv and environment also to
# their own files. `update` exits $FAKE_UPDATE_STATUS.
FAKE_CLAUDE = r"""
(IFS=$'\t'; printf '%s\n' "$*") >>"$FAKE_OUT/claude.calls"
case "${1:-}" in
--version) echo "9.9.9 (Claude Code)" ;;
update) exit "${FAKE_UPDATE_STATUS:-0}" ;;
self-hosted-runner)
	printf '%s\0' "$@" >"$FAKE_OUT/runner.argv"
	env >"$FAKE_OUT/runner.env"
	;;
esac
"""


# A fake curl for the watchdog: takes only the flags it uses (-s, -o FILE,
# --max-time N, one URL), as curl rejects what it doesn't know (exit 2). A port
# with a file in $FAKE_OUT/relays answers with that file's status JSON; any other
# exits $FAKE_CURL_STATUS (7, "couldn't connect", by default).
FAKE_CURL = r"""
echo "$*" >>"$FAKE_OUT/curl.calls"
out=- url=
while (($#)); do
	case "$1" in
	-s) ;;
	-o) out="$2"; shift ;;
	--max-time) [[ "$2" =~ ^[0-9]+$ ]] || exit 2; shift ;;
	-*) echo "curl: unknown option $1" >&2; exit 2 ;;
	*) [[ -z "$url" ]] || exit 2; url="$1" ;;
	esac
	shift
done
[[ "$url" =~ ^http://127\.0\.0\.1:([0-9]+)/__agentproxy/status$ ]] || exit 3
relay="$FAKE_OUT/relays/${BASH_REMATCH[1]}"
[[ -f "$relay" ]] || exit "${FAKE_CURL_STATUS:-7}"
if [[ "$out" == - ]]; then cat "$relay"; else cat "$relay" >"$out"; fi
"""


def install_fake_claude(fake_bin: Path):
    write_executable(fake_bin / "claude", FAKE_CLAUDE)


def runner_argv(out: Path) -> list:
    return (out / "runner.argv").read_text().split("\0")[:-1]


def flags(argv: list) -> dict:
    """--flag value pairs and bare --flags (value True) from the runner's argv."""
    found, index = {}, 1
    while index < len(argv):
        name = argv[index]
        if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
            found[name] = argv[index + 1]
            index += 2
        else:
            found[name] = True
            index += 1
    return found


def env_dir_without_runner_keys(tmp: Path, type_name: str, **overrides: str) -> Path:
    """A copy of infra/gcp with <type>.env's runner settings removed (and
    overrides appended): the case where the two GCP paths used to disagree."""
    env_dir = tmp / "infra"
    env_dir.mkdir()
    shutil.copy(REPO / "infra/gcp/project.env", env_dir)
    lines = [
        line
        for line in (REPO / f"infra/gcp/{type_name}.env").read_text().splitlines()
        if not line.startswith(tuple(f"{key}=" for key in RUNNER_KEYS))
    ]
    lines += [f"{key}={value}" for key, value in overrides.items()]
    (env_dir / f"{type_name}.env").write_text("\n".join(lines) + "\n")
    return env_dir


class ClaudeShTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.out = self.tmp / "out"
        self.fake_bin = self.tmp / "bin"
        self.out.mkdir()
        secret = self.home / ".claude-runner/environment-secret"
        secret.parent.mkdir(parents=True)
        secret.write_text("work-order-jwt")
        install_fake_claude(self.fake_bin)
        # Every Linux image has Homebrew on PATH; the fake records any call.
        write_executable(self.fake_bin / "brew", 'echo "$*" >>"$FAKE_OUT/brew.calls"\n')
        write_executable(self.fake_bin / "xattr", 'echo "$*" >>"$FAKE_OUT/xattr.calls"\n')
        # FAKE_UNAME stands in for the kernel name; anything else is the real one.
        write_executable(
            self.fake_bin / "uname",
            'if [[ "$*" == -s && -n "${FAKE_UNAME:-}" ]]; then echo "$FAKE_UNAME"; '
            'else command -p uname "$@"; fi\n',
        )

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def run_claude_sh(self, **env):
        settings = {"FAKE_OUT": str(self.out), "RUNNER_LABEL": "vm-1"}
        settings.update(env)
        settings = {key: value for key, value in settings.items() if value is not None}
        return subprocess.run(
            ["bash", str(CLAUDE_SH)],
            env=base_env(self.home, self.fake_bin, **settings),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def calls(self, name: str) -> list:
        path = self.out / f"{name}.calls"
        return path.read_text().splitlines() if path.exists() else []

    def started(self, **env) -> dict:
        result = self.run_claude_sh(**env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return flags(runner_argv(self.out))

    # The upgrade (r1.f1).

    def test_linux_runs_claude_update_with_brew_on_path(self):
        result = self.run_claude_sh(FAKE_UNAME="Linux")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("brew"), [])
        self.assertEqual(self.calls("claude")[:2], ["update", "--version"])
        self.assertIn("claude 9.9.9 (Claude Code)", result.stdout)

    def test_macos_upgrades_the_cask_and_clears_quarantine(self):
        result = self.run_claude_sh(FAKE_UNAME="Darwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("brew"), ["upgrade --cask claude-code@latest"])
        self.assertNotIn("update", self.calls("claude"))
        self.assertEqual(self.calls("xattr"), [f"-p com.apple.quarantine {self.fake_bin / 'claude'}",
                                               f"-d com.apple.quarantine {self.fake_bin / 'claude'}"])

    def test_no_upgrade_when_turned_off(self):
        for uname in ("Linux", "Darwin"):
            with self.subTest(uname=uname):
                (self.out / "claude.calls").unlink(missing_ok=True)
                result = self.run_claude_sh(FAKE_UNAME=uname, CLAUDE_UPGRADE="false")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls("brew"), [])
                self.assertEqual(self.calls("claude")[0], "--version")

    def test_a_failed_update_still_starts_the_runner(self):
        result = self.run_claude_sh(FAKE_UNAME="Linux", FAKE_UPDATE_STATUS="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("claude update failed; continuing", result.stderr)
        self.assertEqual(runner_argv(self.out)[0], "self-hosted-runner")

    # runner.env -> flags.

    def test_defaults(self):
        self.started()
        self.assertEqual(
            runner_argv(self.out),
            [
                "self-hosted-runner",
                "--client-label", "vm-1",
                "--base-dir", f"{self.home}/workspace",
                "--capacity", "1",
                "--remove-session-state",
                "--confine-repo-settings", "enforce",
                "--environment-secret-file", f"{self.home}/.claude-runner/environment-secret",
                "--use-anthropic-git-proxy",
                "--configure-git",
                "--release-idle-session-min", "60",
                "--kill-session-after-min", "480",
            ],
        )
        self.assertTrue((self.home / "workspace").is_dir())

    def test_empty_limits_mean_never(self):
        found = self.started(
            CLAUDE_RELEASE_IDLE_SESSION_MIN="", CLAUDE_KILL_SESSION_AFTER_MIN="",
            CLAUDE_EXIT_IF_UNUSED_MIN="", CLAUDE_RETIRE_AT="",
        )
        for flag in ("--release-idle-session-min", "--kill-session-after-min",
                     "--exit-if-unused-min", "--retire-at"):
            self.assertNotIn(flag, found)

    def test_every_setting_maps_to_its_flag(self):
        found = self.started(
            CLAUDE_BASE_DIR=str(self.tmp / "ws"),
            CLAUDE_HEALTH_PORT="9090",
            CLAUDE_LOCK_TO_ACCOUNT="acct-1",
            CLAUDE_CONFINE_REPO_SETTINGS="warn",
            CLAUDE_RELEASE_IDLE_SESSION_MIN="15",
            CLAUDE_KILL_SESSION_AFTER_MIN="120",
            CLAUDE_EXIT_IF_UNUSED_MIN="30",
            CLAUDE_RETIRE_AT="1767000000",
            CLAUDE_CONFIGURE_GIT="false",
            CLAUDE_PUSH_OUTCOME_ON_RELEASE="true",
        )
        self.assertEqual(found["--base-dir"], str(self.tmp / "ws"))
        self.assertEqual(found["--health-port"], "9090")
        self.assertEqual(found["--lock-to-account"], "acct-1")
        self.assertEqual(found["--confine-repo-settings"], "warn")
        self.assertEqual(found["--release-idle-session-min"], "15")
        self.assertEqual(found["--kill-session-after-min"], "120")
        self.assertEqual(found["--exit-if-unused-min"], "30")
        self.assertEqual(found["--retire-at"], "1767000000")
        self.assertNotIn("--configure-git", found)
        self.assertIs(found["--push-outcome-on-release"], True)
        self.assertIs(found["--use-anthropic-git-proxy"], True)

    def test_push_outcome_auto_follows_the_git_proxy(self):
        self.assertNotIn("--push-outcome-on-release", self.started())
        found = self.started(CLAUDE_USE_GIT_PROXY="false")
        self.assertIs(found["--push-outcome-on-release"], True)
        self.assertNotIn("--use-anthropic-git-proxy", found)
        found = self.started(CLAUDE_USE_GIT_PROXY="false", CLAUDE_PUSH_OUTCOME_ON_RELEASE="false")
        self.assertNotIn("--push-outcome-on-release", found)

    def test_secret_from_the_environment_is_left_to_the_runner(self):
        (self.home / ".claude-runner/environment-secret").unlink()
        found = self.started(SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET="host-secret")
        self.assertNotIn("--environment-secret-file", found)

    def test_refuses_without_a_secret(self):
        (self.home / ".claude-runner/environment-secret").unlink()
        result = self.run_claude_sh()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing", result.stderr)
        self.assertEqual(self.calls("claude"), [])


class AgentRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.out = self.tmp / "out"
        self.fake_bin = self.tmp / "bin"
        self.out.mkdir()
        self.runner_env = self.home / ".config/agent-runner/runner.env"
        self.runner_env.parent.mkdir(parents=True)
        # The agent's runner: records its environment, then exits with
        # $FAKE_RUNNER_STATUS, or on SIGTERM records that and exits 0.
        write_executable(
            self.home / "bin/agent-runner-claude",
            'trap \'echo TERM >"$FAKE_OUT/signal"; exit 0\' TERM\n'
            'env >"$FAKE_OUT/env"\n'
            'touch "$FAKE_OUT/started"\n'
            'if [[ -n "${FAKE_RUNNER_WAIT:-}" ]]; then while :; do sleep 0.05; done; fi\n'
            'exit "${FAKE_RUNNER_STATUS:-0}"\n',
        )
        write_executable(self.fake_bin / "sudo", 'echo "$*" >>"$FAKE_OUT/sudo.calls"\n')

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def env(self, **extra):
        return base_env(self.home, self.fake_bin, FAKE_OUT=str(self.out), **extra)

    def run_agent_runner(self, **extra):
        return subprocess.run(
            ["bash", str(AGENT_RUNNER)], env=self.env(**extra), capture_output=True, text=True, timeout=30
        )

    def sudo_calls(self):
        path = self.out / "sudo.calls"
        return path.read_text().splitlines() if path.exists() else []

    def runner_saw(self) -> dict:
        return dict(line.split("=", 1) for line in (self.out / "env").read_text().splitlines() if "=" in line)

    def test_waits_for_runner_env(self):
        self.runner_env.unlink(missing_ok=True)
        result = self.run_agent_runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("waiting for make vm-configure", result.stdout)
        self.assertFalse((self.out / "started").exists())

    def test_refuses_an_unknown_agent(self):
        self.runner_env.write_text("AGENT=nosuchagent\n")
        result = self.run_agent_runner()
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown AGENT=nosuchagent", result.stderr)

    def test_persistent_execs_the_agent_with_runner_env_exported(self):
        self.runner_env.write_text("AGENT=claude\nRUNNER_LABEL=vm-1\nCLAUDE_KILL_SESSION_AFTER_MIN=\n")
        result = self.run_agent_runner(FAKE_RUNNER_STATUS="3")
        self.assertEqual(result.returncode, 3, "exec: the runner's own status")
        seen = self.runner_saw()
        self.assertEqual(seen["RUNNER_LABEL"], "vm-1")
        self.assertEqual(seen["CLAUDE_KILL_SESSION_AFTER_MIN"], "")
        self.assertEqual(seen["AGENT_RUNNER_CONFIG_DIR"], str(self.runner_env.parent))
        self.assertEqual(self.sudo_calls(), [])

    def test_ephemeral_powers_off_after_the_runner_exits(self):
        self.runner_env.write_text("AGENT=claude\nEPHEMERAL=true\n")
        result = self.run_agent_runner(FAKE_RUNNER_STATUS="3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("claude runner exited (3); shutting down", result.stdout)
        self.assertEqual(self.sudo_calls(), ["-n /sbin/shutdown -h now"])

    def test_ephemeral_passes_sigterm_on_and_still_powers_off(self):
        self.runner_env.write_text("AGENT=claude\nEPHEMERAL=true\n")
        process = subprocess.Popen(
            ["bash", str(AGENT_RUNNER)], env=self.env(FAKE_RUNNER_WAIT="1"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not (self.out / "started").exists():
                self.assertLess(time.monotonic(), deadline, "the runner never started")
                time.sleep(0.05)
            os.kill(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual((self.out / "signal").read_text().strip(), "TERM")
        self.assertIn("claude runner exited (0)", stdout)
        self.assertEqual(self.sudo_calls(), ["-n /sbin/shutdown -h now"])


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.out = self.tmp / "out"
        self.fake_bin = self.tmp / "bin"
        self.cache = self.tmp / "cache"
        self.out.mkdir()
        self.sessions = self.tmp / "ws/_sessions"
        self.sessions.mkdir(parents=True)
        self.runner_env = self.home / ".config/agent-runner/runner.env"
        self.runner_env.parent.mkdir(parents=True)
        self.configure("terminate")
        self.add_session("sess1", 41234)
        self.relays = self.out / "relays"
        self.relays.mkdir()
        self.proc_net_tcp = self.tmp / "proc-net-tcp"
        self.listen()
        write_executable(self.fake_bin / "curl", FAKE_CURL)
        write_executable(self.fake_bin / "pkill", 'echo "$*" >>"$FAKE_OUT/pkill.calls"\n')

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def configure(self, action):
        self.runner_env.write_text(
            f"AGENT=claude\nCLAUDE_BASE_DIR={self.tmp / 'ws'}\nWATCHDOG_ACTION={action}\n"
        )

    def add_session(self, session, port):
        (self.sessions / f"{session}.gitconfig").write_text(
            f'[http "https://github.com/"]\n\tproxy = http://127.0.0.1:{port}\n'
            f"\tsslCAInfo = {self.ccr(session) / 'ca-bundle.crt'}\n"
        )

    def ccr(self, session):
        return self.sessions / f"{session}.claude-config/ccr"

    def add_relay(self, port, session, compact=False, **fields):
        """A live relay on port signing for session, as its status endpoint says."""
        status = {
            "enabled": True,
            "port": port,
            "caBundlePath": str(self.ccr(session) / "ca-bundle.crt"),
            "ghShimDir": str(self.ccr(session) / "bin"),
            "readmePath": str(self.ccr(session) / "README.md"),
            "gitConfigInjection": False,
            **fields,
        }
        text = json.dumps(status, separators=(",", ":")) if compact else json.dumps(status, indent=2)
        (self.relays / str(port)).write_text(text + "\n")

    def listen(self, *ports):
        """/proc/net/tcp with these ports listening on 127.0.0.1, plus 8080 on every
        address (Cloud Run's) and an established loopback connection."""
        lines = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid"]
        rows = [(f"0100007F:{port:04X}", "0A") for port in ports]
        rows += [("00000000:1F90", "0A"), ("0100007F:A1B2", "01")]
        for index, (local, state) in enumerate(rows):
            lines.append(f"   {index}: {local} 00000000:0000 {state} 00000000:00000000 00:00000000 00000000  1000")
        self.proc_net_tcp.write_text("\n".join(lines) + "\n")

    def write_session_files(self, session, port):
        """The README and gh shim the runner writes, both naming the relay port."""
        ccr = self.ccr(session)
        (ccr / "bin").mkdir(parents=True)
        readme = ccr / "README.md"
        readme.write_text(f"Proxy at http://127.0.0.1:{port} (set via HTTPS_PROXY).\nNot 127.0.0.1:{port}9.\n")
        shim = write_executable(ccr / "bin/gh", f'export HTTPS_PROXY="http://127.0.0.1:{port}"\n')
        return readme, shim

    def proxy(self, session="sess1"):
        return subprocess.run(
            ["git", "config", "-f", str(self.sessions / f"{session}.gitconfig"),
             "--get", "http.https://github.com/.proxy"],
            capture_output=True, text=True, check=True, env=git_env(),
        ).stdout.strip()

    def run_watchdog(self, **extra):
        return subprocess.run(
            ["bash", str(WATCHDOG)],
            env=base_env(self.home, self.fake_bin, FAKE_OUT=str(self.out),
                         XDG_CACHE_HOME=str(self.cache),
                         AGENT_WATCHDOG_PROC_NET_TCP=str(self.proc_net_tcp), **extra),
            capture_output=True, text=True, timeout=30,
        )

    def pkill_calls(self):
        path = self.out / "pkill.calls"
        return path.read_text().splitlines() if path.exists() else []

    def counter(self, session="sess1"):
        return self.cache / f"agent-runner-watchdog/{session}.failures"

    def test_acts_on_the_second_failure_only(self):
        first = self.run_watchdog()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("port 41234 refused, no live relay found (1/2)", first.stdout)
        self.assertEqual(self.pkill_calls(), [])
        self.assertIn("127.0.0.1:41234/__agentproxy/status", (self.out / "curl.calls").read_text())

        second = self.run_watchdog()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("stopping the runner so the session requeues", second.stderr)
        self.assertEqual(
            self.pkill_calls(), [f"-TERM -u {os.getuid()} -f ^claude self-hosted-runner( |$)"]
        )

        third = self.run_watchdog()
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(len(self.pkill_calls()), 1, "once at the threshold, not every 30 s")
        self.assertEqual(self.counter().read_text().strip(), "3")

    def test_log_only_by_default(self):
        self.runner_env.write_text(f"AGENT=claude\nCLAUDE_BASE_DIR={self.tmp / 'ws'}\n")
        self.run_watchdog()
        result = self.run_watchdog()
        self.assertIn("WATCHDOG_ACTION=log, not acting", result.stderr)
        self.assertEqual(self.pkill_calls(), [])

    def test_an_answering_relay_resets_the_count(self):
        self.run_watchdog()
        self.assertTrue(self.counter().exists())
        # Anything but "couldn't connect" means something answers (28 is a timeout).
        self.run_watchdog(FAKE_CURL_STATUS="28")
        self.assertFalse(self.counter().exists())
        self.run_watchdog()
        self.assertEqual(self.pkill_calls(), [])

    def test_forgets_ended_sessions(self):
        self.run_watchdog()
        (self.sessions / "sess1.gitconfig").unlink()
        self.run_watchdog()
        self.assertFalse(self.counter().exists())

    def test_ignores_a_proxy_that_is_not_local(self):
        (self.sessions / "sess1.gitconfig").write_text(
            '[http "https://github.com/"]\n\tproxy = http://proxy.example.com:3128\n'
        )
        self.run_watchdog()
        self.run_watchdog()
        self.assertFalse((self.out / "curl.calls").exists())
        self.assertEqual(self.pkill_calls(), [])

    def test_repairs_to_the_sessions_live_relay(self):
        readme, shim = self.write_session_files("sess1", 41234)
        # Another session's relay listens first; only the one signing for sess1 counts.
        self.add_session("sess2", 40000)
        self.add_relay(40000, "sess2")
        self.add_relay(39971, "sess1")
        self.listen(40000, 39971)

        result = self.run_watchdog()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("port 41234 refused; repaired to the session's live relay on 39971", result.stderr)
        self.assertEqual(self.proxy(), "http://127.0.0.1:39971")
        self.assertEqual(self.proxy("sess2"), "http://127.0.0.1:40000")
        self.assertEqual(
            readme.read_text(), "Proxy at http://127.0.0.1:39971 (set via HTTPS_PROXY).\nNot 127.0.0.1:412349.\n"
        )
        self.assertIn("127.0.0.1:39971", shim.read_text())
        self.assertTrue(os.access(shim, os.X_OK), "the shim stays executable")
        self.assertFalse(self.counter().exists())
        self.assertEqual(self.pkill_calls(), [])

        # Repaired, the next run finds the port answering and does nothing.
        again = self.run_watchdog()
        self.assertEqual((again.stdout, again.stderr), ("", ""))

    def test_counts_when_no_relay_signs_for_the_session(self):
        self.add_relay(40000, "sess2")
        self.add_relay(39971, "sess1", enabled=False)
        self.listen(40000, 39971)
        first = self.run_watchdog()
        self.assertIn("no live relay found (1/2)", first.stdout)
        self.assertEqual(self.proxy(), "http://127.0.0.1:41234")
        self.run_watchdog()
        self.assertEqual(len(self.pkill_calls()), 1)

    def test_ignores_a_relay_that_reports_another_port(self):
        (self.relays / "39971").write_text(json.dumps({
            "enabled": True, "port": 399710, "caBundlePath": str(self.ccr("sess1") / "ca-bundle.crt"),
        }))
        self.listen(39971)
        self.run_watchdog()
        self.assertEqual(self.proxy(), "http://127.0.0.1:41234")

    def test_edits_only_the_sessions_own_files(self):
        outside = self.tmp / "elsewhere/README.md"
        outside.parent.mkdir()
        outside.write_text("http://127.0.0.1:41234\n")
        self.add_relay(39971, "sess1", readmePath=str(outside), ghShimDir=str(outside.parent))
        self.listen(39971)
        result = self.run_watchdog()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.proxy(), "http://127.0.0.1:39971")
        self.assertEqual(outside.read_text(), "http://127.0.0.1:41234\n")

    def test_finds_relays_with_lsof_on_macos(self):
        write_executable(self.fake_bin / "uname", '[[ "$*" == "-s" ]] || exit 1\necho Darwin\n')
        write_executable(
            self.fake_bin / "lsof",
            '[[ "$*" == "-nP -iTCP@127.0.0.1 -sTCP:LISTEN -Fn" ]] || { echo "lsof: bad args: $*" >&2; exit 1; }\n'
            'printf "p501\\nf12\\nn127.0.0.1:39971\\np502\\nf3\\nn127.0.0.1:5000\\n"\n',
        )
        self.add_relay(39971, "sess1", compact=True)
        self.listen()  # /proc says nothing listens: the port has to come from lsof.
        result = self.run_watchdog()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.proxy(), "http://127.0.0.1:39971")

    def test_nothing_to_do_before_the_vm_is_configured(self):
        self.runner_env.unlink()
        result = self.run_watchdog()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.out / "curl.calls").exists())


class CloudRunChainTests(EntrypointCase):
    """job-spec.sh's container env -> the entrypoint -> agent-runner -> claude.sh."""

    def setUp(self):
        super().setUp()
        install_fake_claude(self.fake_bin)
        shutil.copy(AGENT_RUNNER, self.fake_bin / "agent-runner")
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        shutil.copy(CLAUDE_SH, bin_dir / "agent-runner-claude")

    def job_env(self, env_dir=None) -> dict:
        env = {"PATH": "/usr/bin:/bin"}
        if env_dir:
            env["AGENT_GCP_ENV_DIR"] = str(env_dir)
        result = subprocess.run(
            ["bash", str(JOB_SPEC), "linux", IMAGE], env=env, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        container = json.loads(result.stdout)["template"]["template"]["containers"][0]
        return {item["name"]: item["value"] for item in container["env"]}

    def runner_flags(self, job_env: dict, upgrades: bool = False) -> dict:
        before = int(time.time())
        result = self.run_entrypoint(AGENT_RUNNER_COMMAND=str(self.fake_bin / "agent-runner"), **job_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        found = flags(runner_argv(self.out))
        retire_at = int(found.pop("--retire-at"))
        timeout = int(job_env["AGENT_TASK_TIMEOUT_SEC"]) - int(job_env["AGENT_RETIRE_MARGIN_SEC"])
        self.assertLessEqual(abs(retire_at - (before + timeout)), 5)
        self.assertEqual(found["--base-dir"], f"{self.scratch}/workspace")
        self.assertEqual(found["--client-label"], "linux-abc123")
        # linux is nightly, so it runs the Claude Code its lock baked in (m5, D6);
        # a weekly type updates itself at start.
        calls = (self.out / "claude.calls").read_text().splitlines()
        if upgrades:
            self.assertIn("update", calls)
        else:
            self.assertNotIn("update", calls)
        return found

    def test_a_weekly_type_updates_claude_itself(self):
        env_dir = env_dir_without_runner_keys(self.tmp, "linux", REBUILD="weekly")
        self.runner_flags(self.job_env(env_dir), upgrades=True)

    def test_the_committed_settings_reach_the_runner(self):
        found = self.runner_flags(self.job_env())
        self.assertEqual(found["--release-idle-session-min"], "60")
        self.assertEqual(found["--kill-session-after-min"], "480")
        self.assertEqual(found["--exit-if-unused-min"], "60")

    def test_unset_settings_take_claude_sh_defaults(self):
        env_dir = env_dir_without_runner_keys(self.tmp, "linux")
        found = self.runner_flags(self.job_env(env_dir))
        self.assertEqual(found["--release-idle-session-min"], "60")
        self.assertEqual(found["--kill-session-after-min"], "480")
        self.assertNotIn("--exit-if-unused-min", found)

    def test_empty_settings_mean_never(self):
        env_dir = env_dir_without_runner_keys(
            self.tmp, "linux", RELEASE_IDLE_SESSION_MIN="", KILL_SESSION_AFTER_MIN=""
        )
        found = self.runner_flags(self.job_env(env_dir))
        self.assertNotIn("--release-idle-session-min", found)
        self.assertNotIn("--kill-session-after-min", found)


class ComputeChainTests(unittest.TestCase):
    """The compute hook's runner-env metadata -> agent-gce-order -> agent-runner
    (EPHEMERAL) -> claude.sh."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.out = self.tmp / "out"
        self.fake_bin = self.tmp / "bin"
        for path in (self.home, self.out):
            path.mkdir()
        self.work_order = self.tmp / "work-order"
        self.work_order.write_text("eyJhbGciOi.work-order.signature\n")
        install_fake_claude(self.fake_bin)
        write_executable(self.fake_bin / "sudo", 'echo "$*" >>"$FAKE_OUT/sudo.calls"\n')
        write_executable(self.fake_bin / "fake-poweroff", 'touch "$FAKE_OUT/powered-off"\n')
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        shutil.copy(CLAUDE_SH, bin_dir / "agent-runner-claude")
        self.gcp = FakeGcp().__enter__()

    def tearDown(self):
        self.gcp.__exit__(None, None, None)
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def bash(self, script, **env):
        return subprocess.run(
            ["bash", str(script)],
            env=base_env(self.home, self.fake_bin, FAKE_OUT=str(self.out), **env),
            capture_output=True, text=True, timeout=60,
        )

    def runner_flags(self, env_dir=None) -> dict:
        started = int(time.time())
        hook = self.bash(
            HOOK, **self.gcp.env(), RUNNER_TYPE="android", GCP_POLL_SEC="0.1",
            CLAUDE_RUNNER_WORK_ORDER_FILE=str(self.work_order), CLAUDE_RUNNER_ORDER_ID="ORD0123456789abcdef",
            **({"AGENT_GCP_ENV_DIR": str(env_dir)} if env_dir else {}),
        )
        self.assertEqual(hook.returncode, 0, hook.stderr)
        insert = self.gcp.requests("POST", "/instances")[0].json()
        metadata = {item["key"]: item["value"] for item in insert["metadata"]["items"]}
        self.gcp.state.attributes.update(
            {"work-order": metadata["work-order"], "runner-env": metadata["runner-env"]}
        )

        order = self.bash(
            GCE_ORDER, GCP_METADATA_URL=self.gcp.env()["GCP_METADATA_URL"],
            AGENT_GUEST_USER=getpass.getuser(), AGENT_GUEST_HOME=str(self.home),
            AGENT_POWEROFF_COMMAND=str(self.fake_bin / "fake-poweroff"), AGENT_METADATA_RETRIES="0",
        )
        self.assertEqual(order.returncode, 0, order.stderr)
        self.assertFalse((self.out / "powered-off").exists(), order.stdout + order.stderr)

        # The guest's scratch disk is /scratch; point the checkout somewhere writable.
        runner_env = self.home / ".config/agent-runner/runner.env"
        values = read_env_file(runner_env)
        self.assertEqual(values["CLAUDE_BASE_DIR"], "/scratch/workspace")
        self.assertEqual(values["EPHEMERAL"], "true")
        runner_env.write_text(
            runner_env.read_text().replace("/scratch/workspace", str(self.tmp / "workspace"))
        )

        result = self.bash(AGENT_RUNNER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.out / "sudo.calls").read_text().splitlines(), ["-n /sbin/shutdown -h now"])
        found = flags(runner_argv(self.out))
        retire_at = int(found.pop("--retire-at"))
        self.assertLessEqual(abs(retire_at - (started + 34200 - 900)), 5)
        self.assertEqual(found["--client-label"], "android-ord01234")
        self.assertEqual(found["--environment-secret-file"], f"{self.home}/.claude-runner/environment-secret")
        self.assertIn("update", (self.out / "claude.calls").read_text().splitlines())
        return found

    def test_the_committed_settings_reach_the_runner(self):
        found = self.runner_flags()
        self.assertEqual(found["--release-idle-session-min"], "60")
        self.assertEqual(found["--kill-session-after-min"], "480")
        self.assertEqual(found["--exit-if-unused-min"], "60")

    def test_unset_settings_take_claude_sh_defaults(self):
        # The Cloud Run chain's answer for the same env file (r1.f19).
        found = self.runner_flags(env_dir_without_runner_keys(self.tmp, "android"))
        self.assertEqual(found["--release-idle-session-min"], "60")
        self.assertEqual(found["--kill-session-after-min"], "480")
        self.assertNotIn("--exit-if-unused-min", found)


if __name__ == "__main__":
    unittest.main()
