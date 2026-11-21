"""The host runners' orchestrator hooks and runner jobs, macOS (Tart, launchd) and
Linux (LXD, systemd), with fake tart, launchctl, plutil, lxc, systemctl and
systemd-run: hooks/spawn-runner, hooks/linux/spawn-runner, scripts/runner-once.sh,
scripts/linux/runner-once.sh and reclaim_stale_runners in each lib.sh (r1.f13).

The scripts find the repo from their own path, so each test links them into a
temp tree beside a fake vm-configure.sh: their claims, logs and plists land
there, not in the checkout."""

import os
import plistlib
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable

WORK_ORDER = "eyJhbGciOi.work-order.signature"
ORDER_ID = "ORD0123456789abcdef"
ENV_KEY = "sk-ant-cc-the.environment.key"
# Every script's stop budget for a VM's runner to drain (r1.f30).
VM_STOP_TIMEOUT = "120"

# Each fake appends its argv to $FAKE_OUT/<name>.calls, and notes in
# $FAKE_OUT/leaked if the orchestrator's key reached it.
RECORD = r"""
printf '%s\n' "$*" >>"$FAKE_OUT/$(basename "$0").calls"
[[ -z "${SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET:-}" ]] || echo "$(basename "$0")" >>"$FAKE_OUT/leaked"
"""

# Tart keeps a directory per VM under $TART_HOME/vms (what vm_exists reads).
# `run` holds until the VM is stopped or powers off ($vms/<vm>/stop). Like Tart,
# it refuses an unknown subcommand or option, stopping a VM that isn't running,
# and deleting one that is. `stop` takes --timeout/-t (default 30) and waits for
# the VM to stop.
FAKE_TART = RECORD + r"""
vms="$TART_HOME/vms"
die() { echo "Error: $*" >&2; exit 1; }
running() { [[ -e "$vms/$1/running" ]] && kill -0 "$(<"$vms/$1/running")" 2>/dev/null; }
command="${1:-}"
shift || true
case "$command" in
clone)
	(($# == 2)) || die "usage: tart clone <source-name> <new-name>"
	[[ ! -d "$vms/$2" ]] || die "VM \"$2\" already exists"
	mkdir -p "$vms/$2"
	;;
set)
	name="${1:?}"
	shift
	[[ -d "$vms/$name" ]] || die "the specified VM \"$name\" does not exist"
	while (($#)); do
		case "$1" in
		--cpu | --memory) [[ "${2:-}" =~ ^[0-9]+$ ]] || die "bad $1"; shift 2 ;;
		*) die "Unknown option '$1'" ;;
		esac
	done
	;;
run)
	[[ "${1:-}" == --no-graphics ]] || die "Unknown option '${1:-}'"
	(($# == 2)) || die "usage: tart run [--no-graphics] <name>"
	[[ -d "$vms/$2" ]] || die "the specified VM \"$2\" does not exist"
	echo "$$" >"$vms/$2/running"
	until [[ -e "$vms/$2/stop" ]]; do command -p sleep 0.05; done
	rm -f "$vms/$2/running" "$vms/$2/stop"
	;;
stop)
	while [[ "${1:-}" == -* ]]; do
		case "$1" in
		--timeout | -t) [[ "${2:-}" =~ ^[0-9]+$ ]] || die "bad timeout"; shift 2 ;;
		*) die "Unknown option '$1'" ;;
		esac
	done
	(($# == 1)) || die "usage: tart stop [--timeout <timeout>] <name>"
	[[ -d "$vms/$1" ]] || die "the specified VM \"$1\" does not exist"
	running "$1" || die "VM \"$1\" is not running"
	touch "$vms/$1/stop"
	while running "$1"; do command -p sleep 0.05; done
	;;
delete)
	(($# == 1)) || die "usage: tart delete <name>"
	[[ -d "$vms/$1" ]] || die "the specified VM \"$1\" does not exist"
	running "$1" && die "VM \"$1\" is running"
	rm -rf "${vms:?}/$1"
	;;
exec)
	[[ "${1:-}" != -i ]] || shift
	(($# >= 2)) || die "usage: tart exec [-i] <name> <command>..."
	;;
*) die "Unknown subcommand '$command'" ;;
esac
"""

# launchctl: bootstrap <domain> <plist> (a copy is kept) and bootout
# <domain>/<label> (3, "No such process", unless told otherwise); anything else is
# a usage error (64), as launchctl gives.
FAKE_LAUNCHCTL = RECORD + r"""
usage() { echo "Usage: launchctl $*" >&2; exit 64; }
case "${1:-}" in
bootstrap)
	(($# == 3)) && [[ "$2" =~ ^gui/[0-9]+$ && -f "$3" ]] || usage "bootstrap <domain-target> <path>"
	cp "$3" "$FAKE_OUT/bootstrapped.plist"
	exit "${FAKE_BOOTSTRAP_STATUS:-0}"
	;;
bootout)
	(($# == 2)) && [[ "$2" =~ ^gui/[0-9]+/.+ ]] || usage "bootout <service-target>"
	exit "${FAKE_BOOTOUT_STATUS:-3}"
	;;
*) echo "Unrecognized subcommand: ${1:-}" >&2; exit 64 ;;
esac
"""

# plutil -lint <file>: fails on a plist that doesn't parse.
FAKE_PLUTIL = RECORD + r"""
(($# == 2)) && [[ "$1" == -lint ]] || { echo "plutil: unknown option" >&2; exit 1; }
python3 -I -c 'import plistlib, sys; plistlib.load(open(sys.argv[1], "rb"))' "$2"
"""

# LXD keeps a file per instance under $FAKE_LXD/instances. Existence checks
# (info, list) are numbered: LXD doesn't answer on the ones in
# $FAKE_LXD_DOWN_AT, and the VM powers off (an ephemeral one is gone) from
# $FAKE_POWEROFF_AT on. Unknown commands and flags fail, as lxc's do.
FAKE_LXC = RECORD + r"""
instances="$FAKE_LXD/instances"
die() { echo "Error: $*" >&2; exit 1; }
check() {
	local count
	count=$(($(cat "$FAKE_LXD/checks" 2>/dev/null || echo 0) + 1))
	echo "$count" >"$FAKE_LXD/checks"
	if [[ " ${FAKE_LXD_DOWN_AT:-} " == *" $count "* ]]; then
		die "LXD unix socket \"/var/snap/lxd/common/lxd/unix.socket\" not accessible: connection refused"
	fi
	if [[ -n "${FAKE_POWEROFF_AT:-}" ]] && ((count >= FAKE_POWEROFF_AT)); then
		rm -f "$instances"/*
	fi
}
command="${1:-}"
shift || true
case "$command" in
launch)
	(($# >= 2)) || die "Invalid number of arguments"
	name="$2"
	shift 2
	while (($#)); do
		case "$1" in
		--vm | --ephemeral) ;;
		--config) [[ "${2:-}" == ?*=?* ]] || die "Bad key=value pair: ${2:-}"; shift ;;
		*) die "unknown flag: $1" ;;
		esac
		shift
	done
	[[ ! -e "$instances/$name" ]] || die "Instance \"$name\" already exists"
	mkdir -p "$instances"
	touch "$instances/$name"
	;;
info)
	(($# == 1)) || die "Invalid number of arguments"
	check
	[[ -e "$instances/$1" ]] || die "Instance not found"
	echo "Name: $1"
	;;
list)
	[[ "$*" == "--format csv --columns n" ]] || die "unknown flag or argument: $*"
	check
	ls "$instances" 2>/dev/null || true
	;;
stop)
	if [[ "${1:-}" == --timeout ]]; then
		[[ "${2:-}" =~ ^-?[0-9]+$ ]] || die "invalid argument \"${2:-}\" for \"--timeout\""
		shift 2
	fi
	(($# == 1)) || die "Invalid number of arguments"
	[[ -e "$instances/$1" ]] || die "Instance not found"
	rm -f "$instances/$1"
	;;
delete)
	[[ "${1:-}" != --force ]] || shift
	(($# == 1)) || die "Invalid number of arguments"
	[[ -e "$instances/$1" ]] || die "Instance not found"
	rm -f "$instances/$1"
	;;
exec)
	(($# >= 3)) || die "Invalid number of arguments"
	shift
	[[ "$1" != --force-noninteractive ]] || shift
	[[ "$1" == -- ]] || die "unknown flag: $1"
	;;
*) die "unknown command \"$command\" for \"lxc\"" ;;
esac
"""

# systemctl --user reset-failed|stop <unit>: 5, "Unit not loaded", unless told.
FAKE_SYSTEMCTL = RECORD + r"""
[[ "${1:-}" == --user ]] || { echo "systemctl: --user expected" >&2; exit 1; }
case "${2:-}" in
reset-failed | stop) (($# == 3)) || exit 1; exit "${FAKE_SYSTEMCTL_STATUS:-5}" ;;
*) echo "Unknown command verb ${2:-}." >&2; exit 1 ;;
esac
"""

# systemd-run: the options spawn-runner uses, then the command; keeps the argv
# (NUL-separated) and exits $FAKE_SYSTEMD_RUN_STATUS.
FAKE_SYSTEMD_RUN = RECORD + r"""
printf '%s\0' "$@" >"$FAKE_OUT/systemd-run.argv"
while (($#)); do
	case "$1" in
	--user | --quiet | --collect) shift ;;
	--unit | --property | --working-directory | --setenv)
		[[ -n "${2:-}" ]] || { echo "systemd-run: option $1 needs a value" >&2; exit 1; }
		shift 2
		;;
	-*) echo "systemd-run: unrecognized option '$1'" >&2; exit 1 ;;
	*) break ;;
	esac
done
(($#)) || { echo "systemd-run: command line to execute required" >&2; exit 1; }
exit "${FAKE_SYSTEMD_RUN_STATUS:-0}"
"""

# A short sleep, so a wait loop doesn't hold a test up; it takes what sleep takes.
FAKE_SLEEP = r"""
(($# == 1)) && [[ "$1" =~ ^[0-9]+(\.[0-9]+)?$ ]] || { echo "sleep: invalid time interval" >&2; exit 1; }
command -p sleep 0.01
"""

# vm-configure.sh <--config file> <vm> with the work order, as runner-once calls
# it. Keeps what it was given; FAKE_SESSION_DONE powers a Tart VM off after
# (the guest's session ended); exits $FAKE_CONFIGURE_STATUS.
FAKE_VM_CONFIGURE = RECORD + r"""
(($# == 3)) && [[ "$1" == --config && -f "$2" ]] || { echo "usage: vm-configure.sh [--config <file>] <vm>" >&2; exit 1; }
[[ "${EPHEMERAL:-}" == true ]] || { echo "not ephemeral" >&2; exit 1; }
cp "$ENVIRONMENT_SECRET_FILE" "$FAKE_OUT/configured-with"
if [[ -n "${FAKE_SESSION_DONE:-}" ]]; then touch "$TART_HOME/vms/$3/stop"; fi
exit "${FAKE_CONFIGURE_STATUS:-0}"
"""

LINKED = (
    "hooks/spawn-runner",
    "hooks/linux/spawn-runner",
    "scripts/lib.sh",
    "scripts/runner-once.sh",
    "scripts/linux/lib.sh",
    "scripts/linux/runner-once.sh",
)


class HostCase(unittest.TestCase):
    """A temp tree with the real scripts linked in, and fakes first on PATH."""

    linux = False

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.tree = self.tmp / "agent-images"
        for relative in LINKED:
            link = self.tree / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(REPO / relative)
        for configure in ("scripts/vm-configure.sh", "scripts/linux/vm-configure.sh"):
            write_executable(self.tree / configure, FAKE_VM_CONFIGURE)
        (self.tree / "vms").mkdir()
        (self.tree / "vms/runner.env").write_text("RUNNER_LABEL_PREFIX=test\n")
        self.runners = self.tree / "build/runners"
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.bin = self.tmp / "bin"
        for name, body in (
            ("tart", FAKE_TART), ("launchctl", FAKE_LAUNCHCTL), ("plutil", FAKE_PLUTIL),
            ("lxc", FAKE_LXC), ("systemctl", FAKE_SYSTEMCTL),
            ("systemd-run", FAKE_SYSTEMD_RUN), ("sleep", FAKE_SLEEP),
        ):
            write_executable(self.bin / name, body)
        self.tart_home = self.tmp / "tart"
        self.lxd = self.tmp / "lxd"
        self.lxd.mkdir()
        self.work_order = self.tmp / "orchestrator-work-order"
        self.work_order.write_text(WORK_ORDER + "\n")
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait()
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def env(self, **extra):
        settings = {
            "FAKE_OUT": str(self.out),
            "FAKE_LXD": str(self.lxd),
            "TART_HOME": str(self.tart_home),
        }
        settings.update(extra)
        settings = {key: value for key, value in settings.items() if value is not None}
        return base_env(self.tmp / "home", self.bin, **settings)

    def calls(self, name):
        path = self.out / f"{name}.calls"
        return path.read_text().splitlines() if path.exists() else []

    def assert_no_leak(self, result_text=""):
        self.assertFalse((self.out / "leaked").exists(), "the environment key reached a command")
        self.assertNotIn(ENV_KEY, result_text)

    # VMs.

    def add_vm(self, vm):
        if self.linux:
            (self.lxd / "instances").mkdir(exist_ok=True)
            (self.lxd / "instances" / vm).touch()
        else:
            (self.tart_home / "vms" / vm).mkdir(parents=True)

    def vm_exists(self, vm):
        if self.linux:
            return (self.lxd / "instances" / vm).exists()
        return (self.tart_home / "vms" / vm).is_dir()

    def claim(self, vm, order_id="ORD-other", pid=None, age_minutes=0):
        claim_dir = self.runners / vm
        claim_dir.mkdir(parents=True)
        (claim_dir / "order-id").write_text(order_id + "\n")
        if pid is not None:
            (claim_dir / "pid").write_text(f"{pid}\n")
        if age_minutes:
            past = time.time() - age_minutes * 60
            os.utime(claim_dir, (past, past))
        return claim_dir


class SpawnRunnerCase(HostCase):
    """The orchestrator hook's contract: 0 submitted or already running, 1 retry,
    2 don't retry; the claim is released on any failure before the submit."""

    def hook(self):
        return self.tree / ("hooks/linux/spawn-runner" if self.linux else "hooks/spawn-runner")

    def run_hook(self, **env):
        settings = {
            "CLAUDE_RUNNER_WORK_ORDER_FILE": str(self.work_order),
            "CLAUDE_RUNNER_ORDER_ID": ORDER_ID,
            "CLAUDE_RUNNER_SESSION_ID": "session_01XYZ",
            "SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET": ENV_KEY,
            "RUNNER_MAX": "2",
        }
        settings.update(env)
        result = subprocess.run(
            ["bash", str(self.hook())],
            env=self.env(**settings),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assert_no_leak(result.stdout + result.stderr)
        self.assertNotIn(WORK_ORDER, result.stdout + result.stderr)
        return result

    def submits(self):
        return self.calls("systemd-run" if self.linux else "launchctl")

    def submitted_command(self):
        if self.linux:
            argv = (self.out / "systemd-run.argv").read_text().split("\0")[:-1]
            return argv[argv.index("/bin/bash"):]
        with (self.out / "bootstrapped.plist").open("rb") as plist:
            return plistlib.load(plist)["ProgramArguments"]

    def fail_submit(self):
        return {"FAKE_SYSTEMD_RUN_STATUS": "1"} if self.linux else {"FAKE_BOOTSTRAP_STATUS": "5"}

    def test_claims_the_first_free_name_and_submits_runner_once(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        claim_dir = self.runners / "runner-1"
        self.assertEqual((claim_dir / "order-id").read_text(), ORDER_ID + "\n")
        self.assertEqual((claim_dir / "work-order").read_text(), WORK_ORDER + "\n")
        self.assertEqual((claim_dir / "work-order").stat().st_mode & 0o777, 0o600)
        script = "scripts/linux/runner-once.sh" if self.linux else "scripts/runner-once.sh"
        self.assertEqual(self.submitted_command(), ["/bin/bash", f"{self.tree}/{script}", "runner-1"])
        self.assertIn(f"runner-1: submitted for order {ORDER_ID} (session session_01XYZ)", result.stdout)

    def test_a_redelivered_order_submits_nothing(self):
        self.claim("runner-2", order_id=ORDER_ID)
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"order {ORDER_ID} already on runner-2", result.stdout)
        self.assertEqual([call for call in self.submits() if "bootout" not in call], [])
        self.assertFalse((self.runners / "runner-1").exists(), "no second claim")

    def test_all_names_busy_retries(self):
        self.claim("runner-1")
        self.claim("runner-2")
        result = self.run_hook()
        self.assertEqual(result.returncode, 1)
        self.assertIn("all 2 runner VMs are busy", result.stderr)
        self.assertEqual(self.submits(), [])
        self.assertEqual(sorted(path.name for path in self.runners.iterdir()), ["runner-1", "runner-2"])

    def test_a_vm_outside_the_claims_is_skipped(self):
        self.add_vm("runner-1")
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.runners / "runner-1").exists())
        self.assertEqual(self.submitted_command()[-1], "runner-2")

    def test_a_failed_submit_releases_the_claim_and_retries(self):
        result = self.run_hook(**self.fail_submit())
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not submit the runner job for runner-1", result.stderr)
        self.assertEqual(list(self.runners.iterdir()), [], "claim released")

    def test_an_unexpected_failure_releases_the_claim_and_retries(self):
        # cp crashes copying the work order (exit 139): set -e, not one of the
        # hook's own exits, and a status the orchestrator would read as "never retry".
        write_executable(self.bin / "cp", 'kill -SEGV $$\n')
        result = self.run_hook()
        self.assertEqual(result.returncode, 1, "never 2 for an unexpected error")
        self.assertIn("spawn-runner failed", result.stderr)
        self.assertEqual(list(self.runners.iterdir()), [], "claim released")
        self.assertEqual(self.submits(), [])

    def test_no_runner_config_does_not_retry(self):
        (self.tree / "vms/runner.env").unlink()
        result = self.run_hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn("no vms/runner.env", result.stderr)
        self.assertFalse(self.runners.exists() and any(self.runners.iterdir()))

    def test_an_empty_work_order_retries(self):
        self.work_order.write_text("")
        result = self.run_hook()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.submits(), [])


class TartSpawnRunnerTests(SpawnRunnerCase):
    def test_the_job_plist_lints_and_does_not_restart(self):
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertIn("-lint", self.calls("plutil")[0])
        plist = (self.out / "bootstrapped.plist").read_text()
        self.assertIn("<key>Label</key><string>com.agent-images.runner-1</string>", plist)
        self.assertIn("<key>KeepAlive</key><false/>", plist)
        bootstrap = [call for call in self.calls("launchctl") if call.startswith("bootstrap")]
        self.assertEqual(len(bootstrap), 1)

    def test_launchd_waits_for_the_vm_to_stop_and_be_deleted(self):
        self.assertEqual(self.run_hook().returncode, 0)
        with (self.out / "bootstrapped.plist").open("rb") as plist:
            exit_timeout = plistlib.load(plist)["ExitTimeOut"]
        self.assertGreater(exit_timeout, int(VM_STOP_TIMEOUT), "SIGKILL before the delete (r1.f30)")


class LxdSpawnRunnerTests(SpawnRunnerCase):
    linux = True

    def test_the_unit_is_named_for_the_vm(self):
        self.assertEqual(self.run_hook().returncode, 0)
        argv = (self.out / "systemd-run.argv").read_text().split("\0")
        self.assertEqual(argv[argv.index("--unit") + 1], "agent-images-runner-1.service")
        self.assertIn("--user", argv)

    def test_systemd_waits_for_the_vm_to_stop_and_be_deleted(self):
        self.assertEqual(self.run_hook().returncode, 0)
        argv = (self.out / "systemd-run.argv").read_text().split("\0")
        stop_sec = [value for value in argv if value.startswith("TimeoutStopSec=")]
        self.assertEqual(len(stop_sec), 1)
        self.assertGreater(int(stop_sec[0].split("=")[1]), int(VM_STOP_TIMEOUT))


class RunnerOnceCase(HostCase):
    """runner-once: start the VM, configure it with the work order, wait for its
    session to end, then delete it and release the name; SIGTERM does it early."""

    def script(self):
        return self.tree / ("scripts/linux/runner-once.sh" if self.linux else "scripts/runner-once.sh")

    def start(self, vm="runner-1", **env):
        claim_dir = self.claim(vm, order_id=ORDER_ID)
        (claim_dir / "work-order").write_text(WORK_ORDER + "\n")
        self.log = self.tmp / "runner-once.log"
        with self.log.open("w") as log:
            child = subprocess.Popen(
                ["bash", str(self.script()), vm],
                env=self.env(**env),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        self.children.append(child)
        return child

    def finish(self, child, timeout=30):
        child.wait(timeout=timeout)
        return child.returncode, self.log.read_text()

    def wait_for_log(self, child, text, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text in self.log.read_text():
                return
            self.assertIsNone(child.poll(), f"exited before {text!r}:\n{self.log.read_text()}")
            time.sleep(0.05)
        self.fail(f"no {text!r} in:\n{self.log.read_text()}")

    def stops(self):
        return [call for call in self.calls("lxc" if self.linux else "tart") if call.startswith("stop")]

    def assert_released(self, vm="runner-1"):
        self.assertFalse(self.vm_exists(vm), "VM deleted")
        self.assertFalse((self.runners / vm).exists(), "name released")

    def test_sigterm_stops_and_deletes_the_vm(self):
        child = self.start()
        self.wait_for_log(child, "waiting for its session to finish")
        self.assertTrue(self.vm_exists("runner-1"))
        child.send_signal(signal.SIGTERM)
        status, log = self.finish(child)
        self.assertEqual(status, 130, log)
        self.assertEqual(self.stops(), [f"stop --timeout {VM_STOP_TIMEOUT} runner-1"])
        self.assert_released()
        self.assertIn("runner-1: deleted", log)

    def test_a_failed_configure_deletes_the_vm(self):
        child = self.start(FAKE_CONFIGURE_STATUS="1")
        status, log = self.finish(child)
        self.assertEqual(status, 0, log)
        self.assertIn("configure failed", log)
        self.assert_released()

    def test_the_work_order_goes_to_configure_then_off_disk(self):
        child = self.start(FAKE_CONFIGURE_STATUS="1")
        self.finish(child)
        self.assertEqual((self.out / "configured-with").read_text(), WORK_ORDER + "\n")
        self.assertNotIn(WORK_ORDER, self.log.read_text())

    def test_an_unclaimed_name_is_refused(self):
        result = subprocess.run(
            ["bash", str(self.script()), "runner-7"], env=self.env(),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not claimed", result.stderr)
        self.assertEqual(self.calls("tart") + self.calls("lxc"), [])

    def test_a_name_that_is_not_a_runner_is_refused(self):
        result = subprocess.run(
            ["bash", str(self.script()), "my-dev-vm"], env=self.env(),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a runner name", result.stderr)


class TartRunnerOnceTests(RunnerOnceCase):
    def test_a_finished_session_deletes_the_vm_without_a_stop(self):
        child = self.start(FAKE_SESSION_DONE="1")
        status, log = self.finish(child)
        self.assertEqual(status, 0, log)
        self.assertEqual(self.stops(), [])
        self.assertIn("delete runner-1", self.calls("tart"))
        self.assert_released()


class LxdRunnerOnceTests(RunnerOnceCase):
    linux = True

    def test_a_powered_off_vm_ends_the_wait(self):
        child = self.start(FAKE_POWEROFF_AT="3")
        status, log = self.finish(child)
        self.assertEqual(status, 0, log)
        self.assertEqual(self.stops(), [])
        self.assert_released()

    def test_a_failed_lxc_does_not_end_the_wait(self):
        # LXD doesn't answer on the 2nd and 3rd checks (a snap refresh) while the
        # VM runs on; it powers off at the 5th. One failed lxc used to end the
        # wait and stop the still-running VM (r1.f13).
        child = self.start(FAKE_LXD_DOWN_AT="2 3", FAKE_POWEROFF_AT="5")
        status, log = self.finish(child)
        self.assertEqual(status, 0, log)
        self.assertEqual([call for call in self.calls("lxc") if call.startswith(("stop", "delete"))], [],
                         "a running VM was stopped or deleted")
        self.assertGreaterEqual(int((self.lxd / "checks").read_text()), 5, "waited until it was gone")
        self.assertEqual(log.count("LXD isn't answering; still waiting"), 1)
        self.assertIn("LXD answers again", log)
        self.assert_released()


class ReclaimCase(HostCase):
    """reclaim_stale_runners: deletes VMs whose runner job is gone and releases
    their names, but spares a live job's claim and a fresh claim with no PID."""

    def reclaim(self, *args):
        lib = self.tree / ("scripts/linux/lib.sh" if self.linux else "scripts/lib.sh")
        result = subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; shift; reclaim_stale_runners "$@"', "_",
             str(lib), *args],
            env=self.env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def dead_pid(self):
        child = subprocess.Popen(["true"])
        child.wait()
        return child.pid

    def live_pid(self):
        child = subprocess.Popen(["sleep", "60"])
        self.children.append(child)
        return child.pid

    def all_calls(self):
        return self.calls("tart") + self.calls("launchctl") + self.calls("lxc") + self.calls("systemctl")

    def test_reclaims_a_dead_job_and_spares_a_live_one(self):
        self.claim("runner-1", pid=self.live_pid())
        self.add_vm("runner-1")
        self.claim("runner-2", pid=self.dead_pid())
        self.add_vm("runner-2")
        result = self.reclaim()
        self.assertTrue((self.runners / "runner-1").exists())
        self.assertTrue(self.vm_exists("runner-1"))
        self.assertEqual([call for call in self.all_calls() if "runner-1" in call], [])
        self.assertIn("runner-2: reclaiming", result.stdout)
        self.assert_reclaimed("runner-2")

    def test_a_fresh_claim_with_no_pid_waits_out_the_grace(self):
        self.claim("runner-1")
        self.claim("runner-2", age_minutes=5)
        self.reclaim()
        self.assertTrue((self.runners / "runner-1").exists(), "the hook may have just made it")
        self.assertFalse((self.runners / "runner-2").exists())
        self.reclaim("0")
        self.assertFalse((self.runners / "runner-1").exists())

    def assert_reclaimed(self, vm):
        self.assertFalse((self.runners / vm).exists())
        self.assertFalse(self.vm_exists(vm))


class TartReclaimTests(ReclaimCase):
    def assert_reclaimed(self, vm):
        super().assert_reclaimed(vm)
        self.assertIn(f"bootout gui/{os.getuid()}/com.agent-images.{vm}", self.calls("launchctl"))
        self.assertIn(f"delete {vm}", self.calls("tart"))

    def test_a_running_vm_gets_the_drain_budget(self):
        # runner-once was killed but its `tart run` lives on, mid-session.
        self.claim("runner-1", pid=self.dead_pid())
        self.add_vm("runner-1")
        run = subprocess.Popen([str(self.bin / "tart"), "run", "--no-graphics", "runner-1"], env=self.env())
        self.children.append(run)
        deadline = time.monotonic() + 10
        while not (self.tart_home / "vms/runner-1/running").exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        self.reclaim()
        self.assertEqual(run.wait(timeout=10), 0)
        self.assertIn(f"stop --timeout {VM_STOP_TIMEOUT} runner-1", self.calls("tart"))
        self.assert_reclaimed("runner-1")


class LxdReclaimTests(ReclaimCase):
    linux = True

    def assert_reclaimed(self, vm):
        super().assert_reclaimed(vm)
        self.assertIn(f"--user stop agent-images-{vm}.service", self.calls("systemctl"))
        self.assertIn(f"delete --force {vm}", self.calls("lxc"))


del SpawnRunnerCase, RunnerOnceCase, ReclaimCase

if __name__ == "__main__":
    unittest.main()
