#!/bin/bash
# Shared helpers for host-side scripts. Sourced, not executed.
# Needs tart, launchctl, and the macOS `security` tool; the orchestrator also
# needs claude.

AGENT_USER="${AGENT_USER:-agent}"
readonly KEYCHAIN_SERVICE_PREFIX="agent-images"

# Hooks and LaunchAgents run these scripts outside make, so paths come from here.
AGENT_IMAGES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly AGENT_IMAGES_DIR
# shellcheck disable=SC2034 # used by the scripts that source this
readonly AGENT_IMAGES_LOG_DIR="$AGENT_IMAGES_DIR/build/logs"
# One directory per claimed runner VM name: the claim itself (mkdir is atomic),
# plus its order id, runner-once PID, and the work order while it's needed.
readonly RUNNERS_DIR="$AGENT_IMAGES_DIR/build/runners"
# Settings for every orchestrator-spawned runner VM.
# shellcheck disable=SC2034 # used by the scripts that source this
readonly RUNNER_CONFIG="$AGENT_IMAGES_DIR/vms/runner.env"

die() {
	printf 'error: %s\n' "$*" >&2
	exit 1
}

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# Waits until the guest agent answers, so tart exec works.
wait_for_guest() {
	local target_vm="$1" timeout="${2:-180}" waited=0
	until tart exec "$target_vm" true >/dev/null 2>&1; do
		((waited >= timeout)) && die "$target_vm: guest agent not reachable after ${timeout}s (is the VM running?)"
		sleep 5
		waited=$((waited + 5))
	done
}

# Prints a secret from the host Keychain: the VM-specific item if present,
# else the "default" one. Returns non-zero if neither exists.
keychain_secret() {
	local service="$KEYCHAIN_SERVICE_PREFIX.$1" target_vm="$2"
	security find-generic-password -s "$service" -a "$target_vm" -w 2>/dev/null ||
		security find-generic-password -s "$service" -a default -w 2>/dev/null
}

# Writes stdin to a path under the agent user's home, owner-only. The content
# travels over stdin so secrets never appear in a process list.
guest_write() {
	local target_vm="$1" relative_path="$2"
	# shellcheck disable=SC2016 # expanded by the guest shell, not here
	tart exec -i "$target_vm" sudo -u "$AGENT_USER" -H /bin/bash -c \
		'umask 077; target="$HOME/$1"; mkdir -p "$(dirname "$target")"; cat >"$target"' \
		_ "$relative_path"
}

# Reads KEY=value from a vms/<vm>.env file without executing it.
env_value() {
	local file="$1" key="$2"
	sed -n -E "s/^[[:space:]]*$key=(.*)$/\\1/p" "$file" | tail -n 1
}

# Prints a setting from the environment, else .env, else the default.
setting() {
	local key="$1" default="$2" value="${!1:-}"
	if [[ -z "$value" && -f "$AGENT_IMAGES_DIR/.env" ]]; then
		value="$(env_value "$AGENT_IMAGES_DIR/.env" "$key")"
	fi
	printf '%s' "${value:-$default}"
}

# Names the orchestrator owns: runner-1 .. runner-N.
is_runner_name() { [[ "$1" =~ ^runner-[0-9]+$ ]]; }

vm_exists() { tart get "$1" >/dev/null 2>&1; }

launchd_domain() { printf 'gui/%s' "$(id -u)"; }

runner_job_label() { printf 'com.agent-images.%s' "$1"; }

# Writes and lints a host LaunchAgent plist. Locals are prefixed so they can't
# collide with a caller's readonly globals.
# Usage: write_host_plist <plist> <label> <log-file> <keep-alive: true|false> <command>...
write_host_plist() {
	local plist_path="$1" job_label="$2" job_log="$3" keep_alive="$4" job_arg
	shift 4
	{
		printf '%s\n' \
			'<?xml version="1.0" encoding="UTF-8"?>' \
			'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
			'<plist version="1.0">' \
			'<dict>' \
			"  <key>Label</key><string>$job_label</string>" \
			'  <key>ProgramArguments</key>' \
			'  <array>'
		for job_arg in "$@"; do
			printf '    <string>%s</string>\n' "$job_arg"
		done
		# ExitTimeOut leaves time to stop and delete a VM after SIGTERM.
		printf '%s\n' \
			'  </array>' \
			"  <key>WorkingDirectory</key><string>$AGENT_IMAGES_DIR</string>" \
			'  <key>EnvironmentVariables</key>' \
			'  <dict>' \
			'    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>' \
			'  </dict>' \
			'  <key>RunAtLoad</key><true/>' \
			"  <key>KeepAlive</key><$keep_alive/>" \
			'  <key>ThrottleInterval</key><integer>30</integer>' \
			'  <key>ExitTimeOut</key><integer>120</integer>' \
			"  <key>StandardOutPath</key><string>$job_log</string>" \
			"  <key>StandardErrorPath</key><string>$job_log</string>" \
			'</dict>' \
			'</plist>'
	} >"$plist_path"
	plutil -lint "$plist_path" >/dev/null
}

# Deletes runner VMs whose runner-once job is gone (a host crash, a killed job)
# and releases their names.
# Usage: reclaim_stale_runners [grace-minutes]
# A claim with no PID yet is spared for grace-minutes (default 2), since the hook
# may have just made it; pass 0 when no hook can be running.
reclaim_stale_runners() {
	local grace_minutes="${1:-2}" claim_dir claim_vm claim_pid
	for claim_dir in "$RUNNERS_DIR"/runner-*/; do
		[[ -d "$claim_dir" ]] || continue
		claim_vm="$(basename "$claim_dir")"
		claim_pid="$(cat "$claim_dir/pid" 2>/dev/null || true)"
		if [[ -n "$claim_pid" ]] && kill -0 "$claim_pid" 2>/dev/null; then
			continue
		fi
		if [[ -z "$claim_pid" ]] && ((grace_minutes > 0)) &&
			[[ -n "$(find "$claim_dir" -maxdepth 0 -mmin "-$grace_minutes")" ]]; then
			continue
		fi
		log "$claim_vm: reclaiming (its runner job is gone)"
		launchctl bootout "$(launchd_domain)/$(runner_job_label "$claim_vm")" >/dev/null 2>&1 || true
		tart stop "$claim_vm" >/dev/null 2>&1 || true
		if vm_exists "$claim_vm"; then
			tart delete "$claim_vm"
		fi
		rm -rf "$claim_dir"
	done
}
