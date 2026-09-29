#!/bin/bash
# Configures a running VM's runner from vms/<vm>.env and the host Keychain,
# then (re)starts it. Idempotent: re-run after changing either.
#
# Usage: [EPHEMERAL=true] scripts/vm-configure.sh <vm>
#
# EPHEMERAL=true (set by runner-run.sh) makes the guest power off after one
# session instead of restarting its runner.
#
# vms/<vm>.env holds non-secret settings only (see vms/example-*.env).
# Secrets come from Keychain items named agent-images.<secret>, with account
# <vm> for a VM-specific value or "default" for a shared one. Set them with
# `make secret-set NAME=<secret> [VM=<vm>]`.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

readonly vm="${1:?usage: vm-configure.sh <vm>}"
readonly config="$script_dir/../vms/$vm.env"
readonly label="com.agent-images.runner"

[[ -f "$config" ]] || die "no $config; copy one of vms/example-*.env"
agent="$(env_value "$config" AGENT)"
case "$agent" in
claude) ;;
*) die "$config: AGENT must be claude (got '${agent}'); see specs/cursor.md" ;;
esac

require_secret() {
	local name="$1" guest_path="$2"
	local value
	value="$(keychain_secret "$name" "$vm")" ||
		die "no Keychain item agent-images.$name for $vm or default; run make secret-set NAME=$name"
	printf '%s' "$value" | guest_write "$vm" "$guest_path"
}

log "$vm: waiting for guest agent"
wait_for_guest "$vm"

# A stable, per-VM hostname keeps runner names distinct across clones.
log "$vm: setting hostname"
for key in ComputerName HostName LocalHostName; do
	tart exec "$vm" sudo scutil --set "$key" "$vm"
done

log "$vm: writing $agent secrets"
case "$agent" in
claude)
	require_secret claude-environment-secret .claude-runner/environment-secret
	;;
esac

# Written last: its presence is what tells launchd to keep the runner running.
log "$vm: writing runner.env"
{
	cat "$config"
	printf '\nRUNNER_LABEL=%s\n' "$vm"
	if [[ "${EPHEMERAL:-false}" == "true" ]]; then
		printf 'EPHEMERAL=true\n'
	fi
} | guest_write "$vm" .config/agent-runner/runner.env

agent_uid="$(tart exec "$vm" id -u "$AGENT_USER")"
log "$vm: restarting runner"
tart exec "$vm" sudo launchctl kickstart -k "gui/$agent_uid/$label"
log "$vm: configured as $agent"
