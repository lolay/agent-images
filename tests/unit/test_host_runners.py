"""The host runners' orchestrator hooks and runner jobs, macOS (Tart, launchd) and
Linux (LXD, systemd), with fake tart, launchctl, plutil, lxc, systemctl and
systemd-run: hooks/spawn-runner, hooks/linux/spawn-runner, scripts/runner-once.sh,
scripts/linux/runner-once.sh and reclaim_stale_runners in each lib.sh (r1.f13).

The scripts find the repo from their own path, so each test links them into a
temp tree beside a fake vm-configure.sh: their claims, logs and plists land
there, not in the checkout."""

import os
import plistlib
import re
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable
from test_shared_runner import install_fake_claude, install_fake_quarantine_tools, quarantine, quarantined

WORK_ORDER = "eyJhbGciOi.work-order.signature"
ORDER_ID = "ORD0123456789abcdef"
ENV_KEY = "sk-ant-cc-the.environment.key"


def lib_value(name):
    """A constant from scripts/lib.sh, the one source for the stop budgets."""
    return subprocess.run(
        ["bash", "-c", 'source "$1"; printf %s "${!2}"', "_", str(REPO / "scripts/lib.sh"), name],
        capture_output=True, text=True, check=True).stdout


# How long a stop gives a runner VM to drain and shut down (r1.f30).
VM_STOP_TIMEOUT = lib_value("VM_STOP_TIMEOUT_SEC")

# A runner job standing in for runner-once: SIGTERM makes it take a moment to
# "stop its VM", then note in $FAKE_OUT/events that it finished. It writes
# $FAKE_OUT/ready-<vm> once the trap is set: a SIGTERM before then would kill
# bash with the default action (a flake seen on #155's CI), so start_job waits
# for it.
FAKE_JOB = r"""
trap 'command -p sleep 0.5; echo "finished $1" >>"$FAKE_OUT/events"; exit 0' TERM
: >"$FAKE_OUT/ready-$1"
while :; do command -p sleep 0.05; done
"""

# How long start_job waits for a fake job to set its trap.
JOB_READY_TIMEOUT = 5

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
	echo "stop-start $1" >>"$FAKE_OUT/events"
	touch "$vms/$1/stop"
	while running "$1"; do command -p sleep 0.05; done
	command -p sleep "${FAKE_STOP_DELAY:-0}"
	echo "stop-end $1" >>"$FAKE_OUT/events"
	;;
delete)
	(($# == 1)) || die "usage: tart delete <name>"
	[[ -d "$vms/$1" ]] || die "the specified VM \"$1\" does not exist"
	running "$1" && die "VM \"$1\" is running"
	rm -rf "${vms:?}/$1"
	;;
exec)
	# In the guest as its user: id answers as admin, and a shell runs here with
	# $FAKE_OUT/guest as its home; anything else (sudo scutil, launchctl, tail)
	# is only recorded.
	[[ "${1:-}" != -i ]] || shift
	(($# >= 2)) || die "usage: tart exec [-i] <name> <command>..."
	shift
	case "$1" in
	id) if [[ "${2:-}" == -un ]]; then echo admin; else echo 501; fi ;;
	/bin/bash) HOME="$FAKE_OUT/guest" "$@" ;;
	esac
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
	# A job in $FAKE_OUT/jobs/<label> (its PID) gets SIGTERM, as launchd sends.
	(($# == 2)) && [[ "$2" =~ ^gui/[0-9]+/.+ ]] || usage "bootout <service-target>"
	job="$FAKE_OUT/jobs/${2##*/}"
	if [[ -f "$job" ]] && kill -0 "$(<"$job")" 2>/dev/null; then
		echo "signal ${2##*/}" >>"$FAKE_OUT/events"
		kill -TERM "$(<"$job")"
		exit 0
	fi
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

# LXD keeps a file per instance under $FAKE_LXD/instances (holding "ephemeral"
# for one, which stopping deletes), and an alias per file under $FAKE_LXD/aliases
# holding its image's fingerprint. Existence checks (info, list) are numbered:
# LXD doesn't answer on the ones in $FAKE_LXD_DOWN_AT, and the VM powers off (an
# ephemeral one is gone) from $FAKE_POWEROFF_AT on. In exec, a tar from stdin
# keeps the archive's listing in $FAKE_OUT/staged.list, and a provisioner
# (scripts/<name>.sh) writes $FAKE_PROVISIONER_STDERR on stderr. Unknown
# commands and flags fail, as lxc's do.
FAKE_LXC = RECORD + r"""
instances="$FAKE_LXD/instances"
die() { echo "Error: $*" >&2; exit 1; }
check() {
	local count
	count=$(($(cat "$FAKE_LXD/checks" 2>/dev/null || echo 0) + 1))
	echo "$count" >"$FAKE_LXD/checks"
	local down="" left
	[[ " ${FAKE_LXD_DOWN_AT:-} " != *" $count "* ]] || down=yes
	# $FAKE_LXD/down: "always", or how many more checks fail.
	if [[ -f "$FAKE_LXD/down" ]]; then
		left="$(<"$FAKE_LXD/down")"
		if [[ "$left" == always ]]; then
			down=yes
		elif ((left > 0)); then
			echo $((left - 1)) >"$FAKE_LXD/down"
			down=yes
		fi
	fi
	if [[ -n "$down" ]]; then
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
		--vm) ;;
		--ephemeral) ephemeral=ephemeral ;;
		--config) [[ "${2:-}" == ?*=?* ]] || die "Bad key=value pair: ${2:-}"; shift ;;
		--device) [[ "${2:-}" == ?*,?*=?* ]] || die "Bad device override syntax: ${2:-}"; shift ;;
		*) die "unknown flag: $1" ;;
		esac
		shift
	done
	[[ ! -e "$instances/$name" ]] || die "Instance \"$name\" already exists"
	mkdir -p "$instances"
	echo "${ephemeral:-}" >"$instances/$name"
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
	if grep -qx ephemeral "$instances/$1"; then rm -f "$instances/$1"; else echo stopped >"$instances/$1"; fi
	;;
delete)
	[[ "${1:-}" != --force ]] || shift
	(($# == 1)) || die "Invalid number of arguments"
	[[ -e "$instances/$1" ]] || die "Instance not found"
	rm -f "$instances/$1"
	;;
exec)
	# `sudo -u <user> -H <command>` runs here with $FAKE_OUT/guest as its home;
	# anything else (true, hostnamectl, systemctl, journalctl) is only recorded.
	(($# >= 3)) || die "Invalid number of arguments"
	shift
	[[ "$1" != --force-noninteractive ]] || shift
	[[ "$1" == -- ]] || die "unknown flag: $1"
	shift
	if [[ "$1" == tar ]]; then
		[[ "${2:-}" == -xf && "${3:-}" == - ]] || die "tar: usage"
		command -p tar -tf - | sort >>"$FAKE_OUT/staged.list"
	elif [[ "$1" == sudo ]]; then
		[[ "${2:-}" == -u && -n "${3:-}" && "${4:-}" == -H ]] || die "sudo: usage"
		shift 4
		if [[ "${*: -1}" == */scripts/*.sh ]]; then
			echo "provisioner ${*: -1}"
			[[ -z "${FAKE_PROVISIONER_STDERR:-}" ]] || echo "$FAKE_PROVISIONER_STDERR" >&2
		else
			HOME="$FAKE_OUT/guest" "$@"
		fi
	fi
	;;
publish)
	(($# >= 2)) && [[ -e "$instances/$1" && "$2" == local: ]] || die "Invalid arguments"
	[[ "$(cat "$instances/$1")" == stopped ]] || die "The instance is running"
	shift 2
	alias=""
	while (($#)); do
		case "$1" in
		--alias) alias="${2:?}"; shift ;;
		--compression) [[ "${2:-}" == zstd ]] || die "bad compression"; shift ;;
		description=*) ;;
		*) die "unknown flag: $1" ;;
		esac
		shift
	done
	fingerprint="$(printf '%s' "$RANDOM$RANDOM" | sha256sum | cut -c1-64)"
	mkdir -p "$FAKE_LXD/images" "$FAKE_LXD/aliases"
	touch "$FAKE_LXD/images/$fingerprint"
	[[ -z "$alias" ]] || echo "$fingerprint" >"$FAKE_LXD/aliases/$alias"
	;;
image)
	sub="${1:-}"
	shift || true
	resolve() { if [[ -f "$FAKE_LXD/aliases/$1" ]]; then cat "$FAKE_LXD/aliases/$1"; elif [[ -e "$FAKE_LXD/images/$1" ]]; then echo "$1"; else return 1; fi; }
	case "$sub" in
	info)
		fingerprint="$(resolve "$1")" || die "Image not found"
		echo "Fingerprint: $fingerprint"
		;;
	delete)
		fingerprint="$(resolve "$1")" || die "Image not found"
		rm -f "$FAKE_LXD/images/$fingerprint"
		grep -lx "$fingerprint" "$FAKE_LXD/aliases"/* 2>/dev/null | xargs -r rm -f
		;;
	alias)
		case "${1:-}" in
		create) [[ -e "$FAKE_LXD/images/${3:-}" && ! -e "$FAKE_LXD/aliases/$2" ]] || die "alias create"; echo "$3" >"$FAKE_LXD/aliases/$2" ;;
		delete) [[ -f "$FAKE_LXD/aliases/${2:-}" ]] || die "Alias not found"; rm -f "$FAKE_LXD/aliases/$2" ;;
		*) die "unknown command" ;;
		esac
		;;
	*) die "unknown command \"$sub\" for \"lxc image\"" ;;
	esac
	;;
*) die "unknown command \"$command\" for \"lxc\"" ;;
esac
"""

# systemctl --user reset-failed|stop [--no-block] <unit>: 5, "Unit not loaded",
# unless the unit is a job in $FAKE_OUT/jobs/<unit> (its PID): stop sends it
# SIGTERM and, without --no-block, waits for it to exit, as systemctl does.
FAKE_SYSTEMCTL = RECORD + r"""
[[ "${1:-}" == --user ]] || { echo "systemctl: --user expected" >&2; exit 1; }
verb="${2:-}"
shift 2 || exit 1
block=true
if [[ "${1:-}" == --no-block ]]; then
	block=false
	shift
fi
case "$verb" in
reset-failed | stop)
	(($# == 1)) || exit 1
	job="$FAKE_OUT/jobs/$1"
	if [[ "$verb" == stop && -f "$job" ]] && kill -0 "$(<"$job")" 2>/dev/null; then
		echo "signal $1" >>"$FAKE_OUT/events"
		kill -TERM "$(<"$job")"
		if $block; then
			while kill -0 "$(<"$job")" 2>/dev/null; do command -p sleep 0.05; done
		fi
		exit 0
	fi
	exit "${FAKE_SYSTEMCTL_STATUS:-5}"
	;;
*) echo "Unknown command verb $verb." >&2; exit 1 ;;
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

# security find-generic-password -s <service> -a <account> -w: the item in
# $FAKE_KEYCHAIN/<service>.<account>, else 44 (errSecItemNotFound); any other use
# fails.
FAKE_SECURITY = RECORD + r"""
(($# == 6)) && [[ "$1" == find-generic-password && "$2" == -s && "$4" == -a && "$6" == -w ]] ||
	{ echo "security: unknown command or option" >&2; exit 2; }
if [[ -n "${FAKE_KEYCHAIN_LOCKED:-}" ]]; then
	echo "security: SecKeychainSearchCopyNext: User interaction is not allowed." >&2
	exit 36
fi
# $FAKE_KEYCHAIN_DENY: accounts whose item this process may not read (its ACL).
if [[ " ${FAKE_KEYCHAIN_DENY:-} " == *" $5 "* ]]; then
	echo "security: SecKeychainSearchCopyNext: The user name or passphrase you entered is not correct." >&2
	exit 51
fi
item="$FAKE_KEYCHAIN/$3.$5"
[[ -f "$item" ]] || { echo "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain." >&2; exit 44; }
cat "$item"
"""

# The host's scutil --get LocalHostName.
FAKE_SCUTIL = r"""
[[ "$*" == "--get LocalHostName" ]] || { echo "scutil: unknown" >&2; exit 1; }
echo testmac
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
    "images/shared/clear-quarantine.sh",
    "scripts/orchestrator-service.sh",
    "scripts/linux/orchestrator-service.sh",
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
            ("security", FAKE_SECURITY), ("scutil", FAKE_SCUTIL),
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
            "FAKE_KEYCHAIN": str(self.tmp / "keychain"),
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

    def start_job(self, name, job):
        """A fake runner job under launchd label or systemd unit <job>, reaped as
        soon as it exits (so kill -0 doesn't see a zombie). Returns once the job
        has set its SIGTERM trap, so a stop can't kill it before it can finish."""
        script = write_executable(self.tmp / "fake-job", FAKE_JOB)
        ready = self.out / f"ready-{name}"
        child = subprocess.Popen(["bash", str(script), name], env=self.env())
        threading.Thread(target=child.wait, daemon=True).start()
        self.children.append(child)
        deadline = time.monotonic() + JOB_READY_TIMEOUT
        while not ready.exists():
            if child.returncode is not None:
                self.fail(f"fake job {name} exited ({child.returncode}) before setting its trap")
            if time.monotonic() > deadline:
                self.fail(f"fake job {name} didn't set its trap within {JOB_READY_TIMEOUT}s")
            time.sleep(0.01)
        (self.out / "jobs").mkdir(exist_ok=True)
        (self.out / "jobs" / job).write_text(f"{child.pid}\n")
        return child.pid

    def events(self):
        path = self.out / "events"
        return path.read_text().splitlines() if path.exists() else []

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


class StopBudgetTests(unittest.TestCase):
    """The host's stop budgets against the guest's own (static guest files, read
    here): a VM stop outlasts the runner's drain, and a job outlasts its VM stop."""

    def test_the_host_waits_longer_than_the_guest_drains(self):
        with (REPO / "images/macos/files/com.agent-images.runner.plist").open("rb") as plist:
            macos_drain = plistlib.load(plist)["ExitTimeOut"]
        unit = (REPO / "images/android/files/agent-runner.service").read_text()
        linux_drain = int(re.search(r"^TimeoutStopSec=(\d+)$", unit, re.M).group(1))
        drain = int(lib_value("RUNNER_DRAIN_SEC"))
        self.assertEqual((macos_drain, linux_drain), (drain, drain))
        vm_stop = int(lib_value("VM_STOP_TIMEOUT_SEC"))
        self.assertGreaterEqual(vm_stop - drain, 30, "time for the guest OS to shut down")
        self.assertGreater(int(lib_value("RUNNER_JOB_STOP_SEC")), vm_stop)


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

    def test_sigterm_while_lxd_is_down_keeps_the_claim(self):
        # Releasing the name would leave a VM that may be running with no claim.
        child = self.start()
        self.wait_for_log(child, "waiting for its session to finish")
        (self.lxd / "down").write_text("always")
        child.send_signal(signal.SIGTERM)
        status, log = self.finish(child)
        self.assertEqual(status, 130, log)
        self.assertEqual([call for call in self.calls("lxc") if call.startswith(("stop", "delete"))], [])
        self.assertTrue((self.runners / "runner-1").exists(), "claim kept for reclaim")
        self.assertIn("keeping its claim for reclaim", log)

    def test_sigterm_rides_out_a_short_lxd_restart(self):
        child = self.start()
        self.wait_for_log(child, "waiting for its session to finish")
        (self.lxd / "down").write_text("3")
        child.send_signal(signal.SIGTERM)
        status, log = self.finish(child)
        self.assertEqual(status, 130, log)
        self.assertEqual(self.stops(), [f"stop --timeout {VM_STOP_TIMEOUT} runner-1"])
        self.assert_released()

    def test_lxd_down_for_too_long_gives_up_and_keeps_the_claim(self):
        child = self.start(LXD_DOWN_LIMIT_MIN="1")
        self.wait_for_log(child, "waiting for its session to finish")
        (self.lxd / "down").write_text("always")
        status, log = self.finish(child)
        self.assertEqual(status, 1, log)
        self.assertIn("LXD hasn't answered for 1 minutes; giving up", log)
        self.assertTrue((self.runners / "runner-1").exists())


class StopRunnersCase(HostCase):
    """orchestrator-service.sh stop-runners: every runner job is signalled before
    the first has finished stopping its VM, so N runners take one stop budget."""

    def job_name(self, vm):
        return f"agent-images-{vm}.service" if self.linux else f"com.agent-images.{vm}"

    def test_signals_every_runner_before_waiting(self):
        vms = ("runner-1", "runner-2", "runner-3")
        for vm in vms:
            self.claim(vm, pid=self.start_job(vm, self.job_name(vm)))
        script = self.tree / ("scripts/linux/orchestrator-service.sh" if self.linux
                              else "scripts/orchestrator-service.sh")
        result = subprocess.run(["bash", str(script), "stop-runners"], env=self.env(),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        signalled = [index for index, event in enumerate(events) if event.startswith("signal")]
        finished = [index for index, event in enumerate(events) if event.startswith("finished")]
        self.assertEqual((len(signalled), len(finished)), (3, 3), events)
        self.assertLess(max(signalled), min(finished), events)
        self.assertEqual(list(self.runners.iterdir()), [], "every name released")


class TartStopRunnersTests(StopRunnersCase):
    pass


class LxdStopRunnersTests(StopRunnersCase):
    linux = True


class ReclaimCase(HostCase):
    """reclaim_stale_runners: deletes VMs whose runner job is gone and releases
    their names, but spares a live job's claim and a fresh claim with no PID."""

    def reclaim(self, *args, **env):
        lib = self.tree / ("scripts/linux/lib.sh" if self.linux else "scripts/lib.sh")
        result = subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; shift; reclaim_stale_runners "$@"', "_",
             str(lib), *args],
            env=self.env(**env), capture_output=True, text=True, timeout=30)
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

    def test_running_vms_stop_side_by_side(self):
        runs = []
        for vm in ("runner-1", "runner-2"):
            self.claim(vm, pid=self.dead_pid())
            self.add_vm(vm)
            runs.append(subprocess.Popen([str(self.bin / "tart"), "run", "--no-graphics", vm], env=self.env()))
            self.children.append(runs[-1])
        deadline = time.monotonic() + 10
        while not all((self.tart_home / f"vms/{vm}/running").exists() for vm in ("runner-1", "runner-2")):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        self.reclaim(FAKE_STOP_DELAY="0.5")
        events = self.events()
        starts = [index for index, event in enumerate(events) if event.startswith("stop-start")]
        ends = [index for index, event in enumerate(events) if event.startswith("stop-end")]
        self.assertEqual((len(starts), len(ends)), (2, 2), events)
        self.assertLess(max(starts), min(ends), events)
        self.assert_reclaimed("runner-1")
        self.assert_reclaimed("runner-2")


class LxdReclaimTests(ReclaimCase):
    linux = True

    def test_lxd_down_keeps_the_claim(self):
        self.claim("runner-1", pid=self.dead_pid())
        self.add_vm("runner-1")
        (self.lxd / "down").write_text("always")
        result = self.reclaim()
        self.assertIn("LXD isn't answering; keeping the claim", result.stderr)
        self.assertTrue((self.runners / "runner-1").exists())
        self.assertNotIn("delete --force runner-1", self.calls("lxc"))

    def assert_reclaimed(self, vm):
        super().assert_reclaimed(vm)
        self.assertIn(f"--user stop agent-images-{vm}.service", self.calls("systemctl"))
        self.assertIn(f"delete --force {vm}", self.calls("lxc"))


class VmConfigureCase(HostCase):
    """vm-configure without a work order: the VM's own key, never the
    orchestrator's, read before anything touches the guest (r1.f24)."""

    VM = "debug-1"

    def setUp(self):
        super().setUp()
        configure = self.tree / ("scripts/linux/vm-configure.sh" if self.linux else "scripts/vm-configure.sh")
        configure.unlink()
        configure.symlink_to(REPO / configure.relative_to(self.tree))
        self.configure = configure
        (self.tree / f"vms/{self.VM}.env").write_text("AGENT=claude\n")
        self.guest_secret = self.out / "guest/.claude-runner/environment-secret"

    def set_key(self, account, value):
        """make secret-set NAME=claude-environment-secret [VM=<account>]."""
        if self.linux:
            name = "claude-environment-secret" + ("" if account == "default" else f".{account}")
            path = self.tmp / "home/.config/agent-images" / name
        else:
            path = self.tmp / "keychain" / f"agent-images.claude-environment-secret.{account}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def run_configure(self, **env):
        return subprocess.run(
            ["bash", str(self.configure), self.VM], env=self.env(**env),
            capture_output=True, text=True, timeout=30)

    def guest_calls(self):
        return self.calls("lxc" if self.linux else "tart")

    def test_no_key_of_its_own_fails_before_touching_the_guest(self):
        for account in ("default", "orchestrator"):
            with self.subTest(orchestrator_account=account):
                self.set_key(account, ENV_KEY)
                result = self.run_configure()
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"make secret-set NAME=claude-environment-secret VM={self.VM}", result.stderr)
                self.assertIn("never the orchestrator's", result.stderr)
                self.assertEqual(self.guest_calls(), [], "nothing reached the guest")
                self.assertFalse(self.guest_secret.exists())

    def test_a_copy_of_the_orchestrators_key_is_refused(self):
        for account in ("default", "orchestrator"):
            with self.subTest(orchestrator_account=account):
                self.set_key(account, ENV_KEY)
                self.set_key(self.VM, ENV_KEY)
                result = self.run_configure()
                self.assertEqual(result.returncode, 1)
                self.assertIn("is the orchestrator's environment key", result.stderr)
                self.assertEqual(self.guest_calls(), [])
                self.assertFalse(self.guest_secret.exists())
                for path in (self.tmp / "keychain", self.tmp / "home/.config"):
                    subprocess.run(["rm", "-rf", str(path)], check=True)

    def test_the_vms_own_key_is_written(self):
        self.set_key("default", ENV_KEY)
        self.set_key(self.VM, "sk-ant-cc-a.separate.key")
        result = self.run_configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.guest_secret.read_text(), "sk-ant-cc-a.separate.key")
        self.assertEqual(self.guest_secret.stat().st_mode & 0o777, 0o600)
        runner_env = (self.out / "guest/.config/agent-runner/runner.env").read_text()
        self.assertIn("RUNNER_LABEL=", runner_env)
        self.assertNotIn(ENV_KEY, result.stdout + result.stderr)

    def test_a_host_keys_account_is_not_a_vm_name(self):
        for vm in ("orchestrator", "default", "host", "host-work"):
            with self.subTest(vm=vm):
                (self.tree / f"vms/{vm}.env").write_text("AGENT=claude\n")
                self.set_key(vm, "sk-ant-cc-a.host.key")
                result = subprocess.run(["bash", str(self.configure), vm], env=self.env(),
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1)
                self.assertIn("name the VM something else", result.stderr)
                self.assertEqual(self.guest_calls(), [])
                self.assertFalse(self.guest_secret.exists())

    def test_a_work_order_needs_no_key(self):
        self.work_order.write_text(WORK_ORDER + "\n")
        result = self.run_configure(ENVIRONMENT_SECRET_FILE=str(self.work_order), EPHEMERAL="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.guest_secret.read_text(), WORK_ORDER + "\n")

    def test_an_empty_work_order_fails_before_touching_the_guest(self):
        self.work_order.write_text("")
        result = self.run_configure(ENVIRONMENT_SECRET_FILE=str(self.work_order), EPHEMERAL="true")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.guest_calls(), [])

    def test_a_work_order_that_is_a_directory_fails_before_touching_the_guest(self):
        # -s holds for a non-empty directory, which the guest write reads as nothing.
        directory = self.tmp / "work-order-dir"
        directory.mkdir()
        (directory / "x").write_text(WORK_ORDER)
        result = self.run_configure(ENVIRONMENT_SECRET_FILE=str(directory), EPHEMERAL="true")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a file", result.stderr)
        self.assertEqual(self.guest_calls(), [])
        self.assertFalse(self.guest_secret.exists())

    def test_the_orchestrator_still_reads_default(self):
        # orchestrator-run.sh's lookup: account orchestrator, else default.
        self.set_key("default", ENV_KEY)
        lib = self.tree / ("scripts/linux/lib.sh" if self.linux else "scripts/lib.sh")
        lookup = ("host_secret claude-environment-secret orchestrator" if self.linux
                  else "keychain_secret claude-environment-secret orchestrator default")
        orchestrator_run = REPO / ("scripts/linux/orchestrator-run.sh" if self.linux else "scripts/orchestrator-run.sh")
        self.assertIn(f'SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET="$({lookup})"', orchestrator_run.read_text())
        result = subprocess.run(["bash", "-c", f'source "$1"; {lookup}', "_", str(lib)],
                                env=self.env(), capture_output=True, text=True, timeout=30)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, ENV_KEY), result.stderr)


class TartVmConfigureTests(VmConfigureCase):
    def test_an_unreadable_orchestrator_key_stops_the_copy_check(self):
        # The VM's item reads; the orchestrator's can't be (its ACL). The copy
        # check used to compare against "" and let this copy through.
        for account in ("orchestrator", "default"):
            with self.subTest(orchestrator_account=account):
                self.set_key(account, ENV_KEY)
                self.set_key(self.VM, ENV_KEY)
                result = self.run_configure(FAKE_KEYCHAIN_DENY="orchestrator default")
                self.assertEqual(result.returncode, 1)
                self.assertIn("can't read the orchestrator's key", result.stderr)
                self.assertIn("passphrase you entered is not correct", result.stderr, "security's reason")
                self.assertEqual(self.guest_calls(), [])
                self.assertFalse(self.guest_secret.exists())
                subprocess.run(["rm", "-rf", str(self.tmp / "keychain")], check=True)

    def test_a_locked_keychain_says_so(self):
        self.set_key(self.VM, "sk-ant-cc-a.separate.key")
        result = self.run_configure(FAKE_KEYCHAIN_LOCKED="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("the login keychain is locked", result.stderr)
        self.assertNotIn("takes a work order or its own", result.stderr, "it has one; the keychain is locked")
        self.assertEqual(self.guest_calls(), [])


class LxdVmConfigureTests(VmConfigureCase):
    linux = True

    def secrets(self):
        return self.tmp / "home/.config/agent-images"

    def unreadable(self, name):
        """The orchestrator's key file, made unreadable: a directory, which no
        mode lets cat read (mode 000 wouldn't stop root)."""
        path = self.secrets() / name
        path.mkdir(parents=True)
        (path / "x").write_text("x")

    def test_an_unreadable_orchestrator_key_stops_the_copy_check(self):
        for name in ("claude-environment-secret", "claude-environment-secret.orchestrator"):
            with self.subTest(orchestrator_file=name):
                self.unreadable(name)
                self.set_key(self.VM, ENV_KEY)
                result = self.run_configure()
                self.assertEqual(result.returncode, 1)
                self.assertIn("can't read the orchestrator's key", result.stderr)
                self.assertEqual(self.guest_calls(), [])
                self.assertFalse(self.guest_secret.exists())
                subprocess.run(["rm", "-rf", str(self.secrets())], check=True)

    def test_a_vm_key_that_is_a_directory_fails_before_touching_the_guest(self):
        # $(<dir) reads a directory as "" and succeeds; an orchestrator key that
        # isn't "" then passes the copy check and "" would reach the guest.
        self.set_key("default", ENV_KEY)
        self.unreadable(f"claude-environment-secret.{self.VM}")
        result = self.run_configure()
        self.assertEqual(result.returncode, 1)
        self.assertIn("can't read", result.stderr)
        self.assertEqual(self.guest_calls(), [])
        self.assertFalse(self.guest_secret.exists())

    def host_secret(self, account):
        lib = self.tree / "scripts/linux/lib.sh"
        return subprocess.run(
            ["bash", "-c", 'source "$1"; host_secret claude-environment-secret "$2"', "_", str(lib), account],
            env=self.env(), capture_output=True, text=True, timeout=30)

    def test_host_secret_tells_absent_from_unreadable(self):
        self.assertEqual(self.host_secret("orchestrator").returncode, 2, "no file at all")
        self.set_key("default", ENV_KEY)
        result = self.host_secret("orchestrator")
        self.assertEqual((result.returncode, result.stdout), (0, ENV_KEY), "falls back to the plain file")
        self.unreadable("claude-environment-secret.orchestrator")
        result = self.host_secret("orchestrator")
        self.assertEqual(result.returncode, 1, "an unreadable file isn't a reason to use another key")
        self.assertNotIn(ENV_KEY, result.stdout)


class BuildImageTests(HostCase):
    """scripts/linux/build-image.sh: green builds only. Provisioner stderr stops
    it before `lxc publish`, so the live alias and image stay; ALLOW_STDERR=1
    publishes anyway once the lines are printed (r1.f31)."""

    linux = True
    LIVE = "a" * 64

    def setUp(self):
        super().setUp()
        # The whole of images/shared, not just the quarantine check HostCase links.
        subprocess.run(["rm", "-rf", str(self.tree / "images/shared")], check=True)
        for relative in ("version.txt", "scripts/linux/build-image.sh", "images/android/stage.sh",
                         "images/android/staging.txt", "images/shared", "images/android/files",
                         "images/android/toolsets.txt", "images/toolsets", "images/android/scripts"):
            link = self.tree / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(REPO / relative)
        # Off this host, snap has no lxd to refresh.
        write_executable(self.bin / "snap", 'echo "error: no matching snaps installed" >&2\nexit 1\n')
        (self.lxd / "images").mkdir()
        (self.lxd / "aliases").mkdir()
        (self.lxd / "images" / self.LIVE).touch()
        (self.lxd / "aliases/agent-android").write_text(self.LIVE + "\n")

    def build(self, **env):
        return subprocess.run(
            ["bash", str(self.tree / "scripts/linux/build-image.sh")], env=self.env(**env),
            capture_output=True, text=True, timeout=60)

    def live_image(self):
        return (self.lxd / "aliases/agent-android").read_text().strip()

    def publishes(self):
        return [call for call in self.calls("lxc") if call.startswith("publish")]

    def test_a_green_build_publishes_and_moves_the_alias(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("green: no stderr from the provisioners", result.stdout)
        self.assertEqual(len(self.publishes()), 1)
        self.assertNotEqual(self.live_image(), self.LIVE)
        self.assertFalse((self.lxd / "images" / self.LIVE).exists(), "the old image is deleted")
        self.assertFalse((self.lxd / "instances/agent-android-build").exists())

    def test_stderr_stops_before_publish(self):
        result = self.build(FAKE_PROVISIONER_STDERR="E: Unable to locate package nosuch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("E: Unable to locate package nosuch", result.stderr, "the lines are shown")
        self.assertIn("not publishing; agent-android stays as it was", result.stderr)
        self.assertEqual(self.publishes(), [])
        self.assertEqual(self.live_image(), self.LIVE)
        self.assertTrue((self.lxd / "images" / self.LIVE).exists())
        self.assertFalse((self.lxd / "instances/agent-android-build").exists(), "build VM deleted")

    def test_allow_stderr_publishes_after_printing_it(self):
        result = self.build(FAKE_PROVISIONER_STDERR="W: a warning", ALLOW_STDERR="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(result.stderr.index("W: a warning"), result.stderr.index("ALLOW_STDERR=1: publishing"))
        self.assertEqual(len(self.publishes()), 1)
        self.assertNotEqual(self.live_image(), self.LIVE)

    def make_build(self, *args, **env):
        """make build in the tree, with the repo's Makefile: it reads the tree's .env."""
        return subprocess.run(
            ["make", "--no-print-directory", "-f", str(REPO / "Makefile"), "build", *args],
            cwd=self.tree, env=self.env(**env), capture_output=True, text=True, timeout=60)

    def test_make_refuses_allow_stderr_from_env_file(self):
        # make reads .env and exports it to every target; there ALLOW_STDERR would
        # publish every bad build. Every assignment form, not one spelling.
        for line in ("ALLOW_STDERR=1", "ALLOW_STDERR += 1", "export ALLOW_STDERR += 1",
                     "ALLOW_STDERR ?= 1", "ALLOW_STDERR := 1", "override ALLOW_STDERR = 1"):
            with self.subTest(line=line):
                (self.tree / ".env").write_text(f"LINUX_BASE_IMAGE=ubuntu:24.04\n{line}\n")
                result = self.make_build(FAKE_PROVISIONER_STDERR="W: a warning")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ALLOW_STDERR is set in .env", result.stderr)
                self.assertEqual(self.calls("lxc"), [], "refused before anything started")

    def test_make_takes_allow_stderr_from_the_command_line_or_environment(self):
        (self.tree / ".env").write_text("LINUX_BASE_IMAGE=ubuntu:24.04\n")
        for args, env in ((["ALLOW_STDERR=1"], {}), ([], {"ALLOW_STDERR": "1"})):
            with self.subTest(args=args, env=env):
                result = self.make_build(*args, FAKE_PROVISIONER_STDERR="W: a warning", **env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("ALLOW_STDERR=1: publishing", result.stderr)
                self.assertNotEqual(self.live_image(), self.LIVE)
                (self.lxd / "aliases/agent-android").write_text(self.LIVE + "\n")
                (self.lxd / "images" / self.LIVE).touch()
        result = self.make_build(FAKE_PROVISIONER_STDERR="W: a warning")
        self.assertNotEqual(result.returncode, 0, "without it, not green stops")

    def test_stages_the_shared_list(self):
        self.assertEqual(self.build().returncode, 0)
        staged = (self.out / "staged.list").read_text().splitlines()
        for path in ("./scripts/setup-user.sh", "./scripts/install-packages.sh", "./toolsets.txt",
                     "./agent-runner.sh", "./agent-emulator.sh", "./toolsets/install-toolset.sh"):
            self.assertIn(path, staged)
        self.assertFalse([path for path in staged if path.startswith("./gce")], "gce is the Compute Engine build's")
        self.assertFalse((self.tree / "build/android-staging").exists(), "the host copy is removed")


class HostQuarantineTests(HostCase):
    """clear_claude_quarantine in scripts/lib.sh runs the shared check
    (images/shared/clear-quarantine.sh) and stops on a claude it won't clear (r1.f29)."""

    def clear(self, **env):
        install_fake_claude(self.bin)
        install_fake_quarantine_tools(self.bin)
        quarantine(self.out, self.bin / "claude")
        return subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; clear_claude_quarantine', "_",
             str(self.tree / "scripts/lib.sh")],
            env=self.env(**env), capture_output=True, text=True, timeout=30)

    def test_clears_an_anthropic_signed_claude(self):
        result = self.clear()
        self.assertEqual(result.returncode, 0, result.stderr)
        # Through the host's log, so the orchestrator's log line has its timestamp.
        self.assertRegex(result.stdout, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ cleared Homebrew's quarantine on ")
        self.assertEqual(quarantined(self.out), [])
        self.assertEqual(len(self.calls("codesign")), 1)

    def test_stops_on_a_claude_it_will_not_clear(self):
        result = self.clear(FAKE_SIGNER_TEAM="ABCDE12345")
        self.assertEqual(result.returncode, 1)
        self.assertIn("still quarantined; reinstall it", result.stderr)
        self.assertEqual(quarantined(self.out), [str(self.bin / "claude")])


del SpawnRunnerCase, RunnerOnceCase, ReclaimCase, VmConfigureCase, StopRunnersCase

if __name__ == "__main__":
    unittest.main()
