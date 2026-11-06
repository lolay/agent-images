#!/bin/bash
# agent-cloud-entrypoint: the Cloud Run runner images' entrypoint (specs/gcp.md,
# "Images"). One execution serves one session: this prepares the scratch disk,
# moves the work order to a file, writes runner.env, starts the virtual display
# (images with the display toolset) and the watchdog loop, then execs
# agent-runner, whose exit ends the execution.
#
#   agent-cloud-entrypoint          run the runner (what every execution does)
#   agent-cloud-entrypoint smoke    check the image and its mounts, then exit
#                                   0 or 1 (the deploy runs it on agent-<type>-next)
#   agent-cloud-entrypoint smoke pr the same on a GitHub hosted runner (make
#                                   cloud-smoke): smoke tests that need what it
#                                   lacks, a GPU, are skipped, saying so
#
# From the job (scripts/cloud/job-spec.sh renders these from infra/gcp/<type>.env):
#   AGENT_TYPE, AGENT_TASK_TIMEOUT_SEC, AGENT_RETIRE_MARGIN_SEC, AGENT_NOTICE_TZ,
#   AGENT_OUTPUTS_BUCKET (for the gs:// path the SessionStart hook shows),
#   CLAUDE_EXIT_IF_UNUSED_MIN, CLAUDE_RELEASE_IDLE_SESSION_MIN,
#   CLAUDE_KILL_SESSION_AFTER_MIN
# From the hook, per execution:
#   SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET (the work order), RUNNER_LABEL
#
# On Cloud Run everything outside the scratch volume is memory, so TMPDIR, the
# caches and the repo checkouts all live on scratch (profile.sh).
set -euo pipefail

# Paths are overridable so the tests can run this outside a container.
: "${AGENT_SCRATCH:=/scratch}"
: "${AGENT_OUTPUTS_ROOT:=/mnt/outputs}"
: "${AGENT_DATASETS:=/mnt/datasets}"
: "${AGENT_PROFILE:=/etc/profile.d/agent-images.sh}"
: "${AGENT_RUNNER_COMMAND:=$HOME/bin/agent-runner}"
: "${AGENT_WATCHDOG_COMMAND:=$HOME/bin/agent-runner-watchdog}"
: "${AGENT_WATCHDOG_INTERVAL_SEC:=30}"
: "${AGENT_REQUIRE_SCRATCH_MOUNT:=true}"
: "${AGENT_TYPE:=unknown}"
: "${AGENT_DISPLAY_SCREEN:=1920x1080x24}"
: "${AGENT_DISPLAY_WAIT_SEC:=10}"
# images/toolsets/toolset-checks.sh, which the runner Dockerfile installs.
: "${AGENT_TOOLSET_CHECKS:=agent-toolset-checks}"
# What the image contains, written by its last layer (write-versions.sh).
: "${AGENT_VERSIONS_REPORT:=/etc/agent-images/versions.md}"
export AGENT_SCRATCH AGENT_OUTPUTS_ROOT AGENT_DATASETS AGENT_TYPE

log() { printf '%s agent-cloud-entrypoint: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
warn() { log "$@" >&2; }
die() {
	warn "$@"
	exit 1
}

# shellcheck source=images/cloud/runner/profile.sh
source "$AGENT_PROFILE"

# The virtual display, for images with the display toolset: its profile sets
# DISPLAY, and Xvfb serves it for the rest of the execution (it outlives this
# process's exec into the runner). Returns non-zero if it doesn't answer.
start_display() {
	[[ -n "${DISPLAY:-}" ]] && command -v Xvfb >/dev/null 2>&1 || return 0
	Xvfb "$DISPLAY" -screen 0 "$AGENT_DISPLAY_SCREEN" -nolisten tcp >/tmp/xvfb.log 2>&1 &
	local deadline=$((SECONDS + AGENT_DISPLAY_WAIT_SEC))
	until xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; do
		((SECONDS < deadline)) || {
			warn "Xvfb didn't answer on $DISPLAY within ${AGENT_DISPLAY_WAIT_SEC}s: $(tail -n 3 /tmp/xvfb.log 2>/dev/null | tr '\n' ' ')"
			return 1
		}
		sleep 0.2
	done
	log "display $DISPLAY ($AGENT_DISPLAY_SCREEN)"
}

scratch_fstype() { findmnt -n -o FSTYPE --target "$AGENT_SCRATCH" 2>/dev/null || true; }

# Scratch is a dedicated disk; without it every file written takes the session's
# memory, so a missing mount is a broken job, not something to work around.
prepare_scratch() {
	if [[ "$AGENT_REQUIRE_SCRATCH_MOUNT" == "true" ]] && ! mountpoint -q "$AGENT_SCRATCH"; then
		die "$AGENT_SCRATCH is not a mount; the job needs its scratch volume"
	fi
	# Cloud Run mounts it writable. sudo is only a fallback for other runtimes:
	# Cloud Run sets no-new-privileges, which stops sudo altogether.
	if [[ ! -w "$AGENT_SCRATCH" ]]; then
		sudo -n chown "$(id -u):$(id -g)" "$AGENT_SCRATCH" 2>/dev/null || die "can't write $AGENT_SCRATCH"
	fi
	mkdir -p "$AGENT_SCRATCH/workspace" "$TMPDIR" "$XDG_CACHE_HOME"
}

smoke() {
	local scope="${1:-}" failed=0 name why
	# check runs each probe inside an `if`, which turns errexit off for it, so a
	# probe's steps must be chained (`|| return 1`): only the last command decides
	# otherwise.
	check() {
		local name="$1"
		shift
		local detail
		if detail="$("$@" 2>&1)"; then
			printf 'smoke: pass %s (%s)\n' "$name" "${detail//$'\n'/; }"
		else
			printf 'smoke: FAIL %s (%s)\n' "$name" "${detail//$'\n'/; }"
			failed=1
		fi
	}
	# shellcheck disable=SC2317,SC2329 # called through check
	scratch_disk() {
		local fstype
		mountpoint -q "$AGENT_SCRATCH" || {
			echo "not a mount"
			return 1
		}
		fstype="$(scratch_fstype)"
		[[ "$fstype" != "tmpfs" ]] || {
			echo "tmpfs: in memory"
			return 1
		}
		prepare_scratch >/dev/null || return 1
		dd if=/dev/zero of="$TMPDIR/smoke" bs=1M count=64 status=none || {
			rm -f "$TMPDIR/smoke"
			echo "can't write $TMPDIR/smoke"
			return 1
		}
		rm -f "$TMPDIR/smoke" || return 1
		printf '%s, %s free' "$fstype" "$(df -h --output=avail "$AGENT_SCRATCH" | tail -n 1 | tr -d ' ')"
	}
	# shellcheck disable=SC2317,SC2329 # called through check
	outputs_round_trip() {
		local dir="$AGENT_OUTPUTS_ROOT/_smoke" file
		file="$dir/${CLOUD_RUN_EXECUTION:-local}-$$"
		if ! { mkdir -p "$dir" && printf 'smoke\n' >"$file" && [[ "$(cat "$file")" == "smoke" ]]; }; then
			echo "can't write, then read $file"
			return 1
		fi
		rm -f "$file" || return 1
		echo "wrote, read and deleted $file"
	}
	# shellcheck disable=SC2317,SC2329 # called through check
	datasets_read_only() {
		ls "$AGENT_DATASETS" >/dev/null || {
			echo "can't list $AGENT_DATASETS"
			return 1
		}
		if touch "$AGENT_DATASETS/.smoke-write" 2>/dev/null; then
			rm -f "$AGENT_DATASETS/.smoke-write"
			echo "a write succeeded"
			return 1
		fi
		echo "listed; a write was refused"
	}
	# shellcheck disable=SC2317,SC2329 # called through check
	homebrew_writable() {
		local prefix
		prefix="$(brew --prefix)"
		[[ -w "$prefix/Cellar" && -w "$prefix/bin" ]] || {
			echo "$prefix isn't writable by $(id -un)"
			return 1
		}
		printf '%s, %s writable' "$(brew --version | head -n 1)" "$prefix"
	}
	check claude claude --version
	# Every installed toolset's binary checks, then each smoke test as its own
	# check, in install order (toolset-checks.sh runs both).
	check binaries "$AGENT_TOOLSET_CHECKS" versions
	while read -r name; do
		if [[ "$scope" == pr ]] && why="$("$AGENT_TOOLSET_CHECKS" live "$name")"; then
			printf 'smoke: skip smoke-%s (live only: %s)\n' "$name" "$why"
			continue
		fi
		check "smoke-$name" "$AGENT_TOOLSET_CHECKS" smoke "$name" --brief
	done < <("$AGENT_TOOLSET_CHECKS" smoke-list 2>/dev/null)
	check scratch scratch_disk
	check outputs outputs_round_trip
	check datasets datasets_read_only
	# Sessions install with Homebrew: Cloud Run's no-new-privileges flag stops sudo
	# (and so apt), so the session user must own Homebrew's prefix.
	check homebrew homebrew_writable
	return "$failed"
}

if [[ "${1:-}" == "smoke" ]]; then
	[[ "${2:-}" == "" || "${2:-}" == pr ]] || die "usage: agent-cloud-entrypoint smoke [pr]"
	# Checks run as sessions do: with TMPDIR and the caches on scratch, which
	# Cloud Run mounts empty (the display toolset's screenshot check uses mktemp
	# before the scratch check would have made them). The scratch check still
	# reports a missing mount.
	mkdir -p "$TMPDIR" "$XDG_CACHE_HOME" 2>/dev/null || true
	# The display toolset's checks need the display running, as sessions get it.
	start_display || true
	[[ ! -f "$AGENT_VERSIONS_REPORT" ]] || log "versions report: $AGENT_VERSIONS_REPORT"
	if smoke "${2:-}"; then
		log "smoke passed"
		exit 0
	fi
	die "smoke failed"
fi

prepare_scratch
# A session without its display still works, so a failure here only warns.
start_display || true

# The runner releases its session cleanly by this time, ahead of Cloud Run's
# hard stop at the task timeout; the SessionStart hook reads it for the notice.
if [[ -n "${AGENT_TASK_TIMEOUT_SEC:-}" ]]; then
	# No default here: the margin's one source is hooks/gcp/lib.sh's
	# gcp_retire_margin, which job-spec.sh renders into the job.
	: "${AGENT_RETIRE_MARGIN_SEC:?AGENT_RETIRE_MARGIN_SEC is required with AGENT_TASK_TIMEOUT_SEC (job-spec.sh sets both)}"
	now="$(date +%s)"
	AGENT_RETIRE_AT=$((now + AGENT_TASK_TIMEOUT_SEC - AGENT_RETIRE_MARGIN_SEC))
	((AGENT_RETIRE_AT > now)) || die "AGENT_TASK_TIMEOUT_SEC ($AGENT_TASK_TIMEOUT_SEC) is not more than the margin ($AGENT_RETIRE_MARGIN_SEC)"
	export AGENT_RETIRE_AT CLAUDE_RETIRE_AT="$AGENT_RETIRE_AT"
fi

# The work order travels as an execution override. Sessions inherit this
# process's environment, so move it to the file claude.sh reads and drop it.
readonly secret_file="$HOME/.claude-runner/environment-secret"
[[ -n "${SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET:-}" ]] || die "no work order (SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET); the hook starts executions with one"
mkdir -p "$(dirname "$secret_file")"
chmod 700 "$(dirname "$secret_file")"
(
	umask 077
	printf '%s' "$SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET" >"$secret_file"
)
unset SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET

export CLAUDE_BASE_DIR="$AGENT_SCRATCH/workspace"
export RUNNER_LABEL="${RUNNER_LABEL:-$AGENT_TYPE-${CLOUD_RUN_EXECUTION:-$(hostname)}}"

# agent-runner and the watchdog read runner.env, as on the Mac and Linux VMs.
readonly runner_env="$HOME/.config/agent-runner/runner.env"
mkdir -p "$(dirname "$runner_env")"
{
	echo "AGENT=claude"
	for name in RUNNER_LABEL CLAUDE_BASE_DIR CLAUDE_RETIRE_AT CLAUDE_EXIT_IF_UNUSED_MIN \
		CLAUDE_RELEASE_IDLE_SESSION_MIN CLAUDE_KILL_SESSION_AFTER_MIN CLAUDE_UPGRADE \
		CLAUDE_USE_GIT_PROXY CLAUDE_PUSH_OUTCOME_ON_RELEASE CLAUDE_CONFIGURE_GIT \
		CLAUDE_CONFINE_REPO_SETTINGS CLAUDE_LOCK_TO_ACCOUNT WATCHDOG_ACTION; do
		if [[ -v "$name" ]]; then
			printf '%s=%q\n' "$name" "${!name}"
		fi
	done
} >"$runner_env"
chmod 600 "$runner_env"

# No systemd here: the watchdog runs as a loop beside the runner. Its parent is
# this process, which becomes the runner at exec; when the runner exits the loop
# is reparented, and stops.
runner_alive() {
	local stat
	read -r -a stat <"/proc/$BASHPID/stat" && [[ "${stat[3]}" == "$$" ]]
}
(
	while sleep "$AGENT_WATCHDOG_INTERVAL_SEC" && runner_alive; do
		"$AGENT_WATCHDOG_COMMAND" || true
	done
) &

log "starting the $AGENT_TYPE runner as $RUNNER_LABEL (scratch: $(scratch_fstype), retire at ${AGENT_RETIRE_AT:-never})"
exec "$AGENT_RUNNER_COMMAND"
