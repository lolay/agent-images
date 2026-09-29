#!/bin/bash
# Shared helpers for the Linux host's scripts: LXD for VMs, systemd user units
# for jobs, and an owner-only file for the environment secret. Sourced, not
# executed. Sources scripts/lib.sh for the portable helpers (log, die, setting,
# env_value, is_runner_name, paths) and replaces its Tart, launchd, and Keychain
# functions with these.

# shellcheck source=scripts/lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib.sh"

# Where the Keychain would be: owner-only, on this dedicated host.
readonly SECRETS_DIR="$HOME/.config/agent-images"

# The LXD image runner VMs launch from (make build).
# shellcheck disable=SC2034 # used by the scripts that source this
readonly LINUX_IMAGE_ALIAS="agent-linux"

# Prints a secret: the VM-specific file if present, else the default one.
# Returns non-zero if neither exists.
host_secret() {
	local name="$1" target_vm="$2" file
	for file in "$SECRETS_DIR/$name.$target_vm" "$SECRETS_DIR/$name"; do
		if [[ -s "$file" ]]; then
			cat "$file"
			return 0
		fi
	done
	return 1
}

vm_exists() { lxc info "$1" >/dev/null 2>&1; }

# Waits until lxd-agent answers, so lxc exec works.
wait_for_guest() {
	local target_vm="$1" timeout="${2:-180}" waited=0
	until lxc exec "$target_vm" -- true >/dev/null 2>&1; do
		((waited >= timeout)) && die "$target_vm: lxd-agent not reachable after ${timeout}s (is the VM running?)"
		sleep 5
		waited=$((waited + 5))
	done
}

# Writes stdin to a path under the agent user's home, owner-only, like the
# macOS guest_write: over stdin so secrets never appear in a process list, and
# renamed into place so systemd never starts the runner on half a runner.env.
guest_write() {
	local target_vm="$1" relative_path="$2"
	# shellcheck disable=SC2016 # expanded by the guest shell, not here
	lxc exec "$target_vm" --force-noninteractive -- sudo -u "$AGENT_USER" -H /bin/bash -c \
		'umask 077; target="$HOME/$1"; mkdir -p "$(dirname "$target")"; cat >"$target.partial" && mv -f "$target.partial" "$target"' \
		_ "$relative_path"
}

runner_unit() { printf 'agent-images-%s.service' "$1"; }

# Deletes runner VMs whose runner-once unit is gone (a host crash, a killed
# unit) and releases their names. Same contract as the macOS version.
# Usage: reclaim_stale_runners [grace-minutes]
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
		systemctl --user stop "$(runner_unit "$claim_vm")" >/dev/null 2>&1 || true
		if vm_exists "$claim_vm"; then
			lxc delete --force "$claim_vm"
		fi
		rm -rf "$claim_dir"
	done
}
