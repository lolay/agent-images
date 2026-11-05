#!/bin/bash
# agent-cloud-entrypoint: the Cloud Run runner images' entrypoint (specs/gcp.md,
# "Images"). One execution serves one session: this prepares the scratch disk,
# moves the work order to a file, writes runner.env, starts the watchdog loop,
# then execs agent-runner, whose exit ends the execution.
#
#   agent-cloud-entrypoint          run the runner (what every execution does)
#   agent-cloud-entrypoint smoke    check the image and its mounts, then exit
#                                   0 or 1 (the deploy runs it on agent-<type>-next)
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
: "${AGENT_RETIRE_MARGIN_SEC:=900}"
export AGENT_SCRATCH AGENT_OUTPUTS_ROOT AGENT_DATASETS AGENT_TYPE

log() { printf '%s agent-cloud-entrypoint: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
warn() { log "$@" >&2; }
die() {
	warn "$@"
	exit 1
}

# shellcheck source=images/cloud/runner/profile.sh
source "$AGENT_PROFILE"

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
	local failed=0
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
		prepare_scratch >/dev/null
		dd if=/dev/zero of="$TMPDIR/smoke" bs=1M count=64 status=none
		rm -f "$TMPDIR/smoke"
		printf '%s, %s free' "$fstype" "$(df -h --output=avail "$AGENT_SCRATCH" | tail -n 1 | tr -d ' ')"
	}
	# shellcheck disable=SC2317,SC2329 # called through check
	outputs_round_trip() {
		local dir="$AGENT_OUTPUTS_ROOT/_smoke" file
		file="$dir/${CLOUD_RUN_EXECUTION:-local}-$$"
		mkdir -p "$dir"
		printf 'smoke\n' >"$file"
		[[ "$(cat "$file")" == "smoke" ]]
		rm -f "$file"
		echo "wrote, read and deleted $file"
	}
	# shellcheck disable=SC2317,SC2329 # called through check
	datasets_read_only() {
		ls "$AGENT_DATASETS" >/dev/null
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
	# shellcheck disable=SC2317,SC2329 # called through check
	toolchains() {
		# Every installed toolset's checks (install-toolset.sh records them), in
		# install order: `label: command`, which must exit 0. A proxy (cargo from
		# rustup, terraform from tenv) fails if its toolchain wasn't installed.
		local versions=() failed=0 name line label command output
		[[ -s "$AGENT_TOOLSETS_DIR/toolsets.txt" ]] || {
			printf 'no toolsets recorded in %s\n' "$AGENT_TOOLSETS_DIR"
			return 1
		}
		while read -r name; do
			[[ -f "$AGENT_TOOLSETS_DIR/$name.check" ]] || continue
			while IFS= read -r line; do
				[[ -n "$line" && "$line" != \#* ]] || continue
				label="${line%%: *}" command="${line#*: }"
				if output="$(CHECKPOINT_DISABLE=1 bash -c "$command" 2>&1 </dev/null)"; then
					versions+=("$label: $(head -n 1 <<<"$output")")
				else
					versions+=("$label: missing")
					failed=1
				fi
			done <"$AGENT_TOOLSETS_DIR/$name.check"
		done <"$AGENT_TOOLSETS_DIR/toolsets.txt"
		printf '%s\n' "${versions[@]}"
		return "$failed"
	}
	check claude claude --version
	check toolchains toolchains
	check scratch scratch_disk
	check outputs outputs_round_trip
	check datasets datasets_read_only
	# Sessions install with Homebrew: Cloud Run's no-new-privileges flag stops sudo
	# (and so apt), so the session user must own Homebrew's prefix.
	check homebrew homebrew_writable
	return "$failed"
}

if [[ "${1:-}" == "smoke" ]]; then
	if smoke; then
		log "smoke passed"
		exit 0
	fi
	die "smoke failed"
fi

prepare_scratch

# The runner releases its session cleanly by this time, ahead of Cloud Run's
# hard stop at the task timeout; the SessionStart hook reads it for the notice.
if [[ -n "${AGENT_TASK_TIMEOUT_SEC:-}" ]]; then
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
