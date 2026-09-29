#!/bin/bash
# Configures a running VM's runner from a settings file and a registration
# secret, then (re)starts it. Idempotent: re-run after changing either.
#
# Usage: [EPHEMERAL=true] [ENVIRONMENT_SECRET_FILE=<file>] \
#          scripts/vm-configure.sh [--config <file>] <vm>
#
# --config defaults to vms/<vm>.env and holds non-secret settings only (see
# vms/example-claude.env). EPHEMERAL=true (set by runner-once.sh) makes the guest
# power off after one session instead of restarting its runner.
#
# The registration secret is ENVIRONMENT_SECRET_FILE when set (the orchestrator's
# single-use work order), else the Keychain item agent-images.claude-environment-secret
# with account <vm> or "default" (make secret-set NAME=claude-environment-secret).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

usage="usage: vm-configure.sh [--config <file>] <vm>"
config=""
vm=""
while (($# > 0)); do
	case "$1" in
	--config)
		config="${2:?$usage}"
		shift 2
		;;
	-*) die "$usage" ;;
	*)
		vm="$1"
		shift
		;;
	esac
done
[[ -n "$vm" ]] || die "$usage"
config="${config:-$AGENT_IMAGES_DIR/vms/$vm.env}"
readonly vm config
readonly label="com.agent-images.runner"

[[ -f "$config" ]] || die "no $config; copy vms/example-claude.env"
agent="$(env_value "$config" AGENT)"
case "$agent" in
claude) ;;
*) die "$config: AGENT must be claude (got '${agent}'); see specs/cursor.md" ;;
esac

push_registration_secret() {
	local guest_path=".claude-runner/environment-secret" value
	if [[ -n "${ENVIRONMENT_SECRET_FILE:-}" ]]; then
		[[ -s "$ENVIRONMENT_SECRET_FILE" ]] || die "$ENVIRONMENT_SECRET_FILE is missing or empty"
		guest_write "$vm" "$guest_path" <"$ENVIRONMENT_SECRET_FILE"
		return
	fi
	value="$(keychain_secret claude-environment-secret "$vm")" ||
		die "no Keychain item agent-images.claude-environment-secret for $vm or default; run make secret-set NAME=claude-environment-secret"
	printf '%s' "$value" | guest_write "$vm" "$guest_path"
}

log "$vm: waiting for guest agent"
wait_for_guest "$vm" "$(setting VM_BOOT_TIMEOUT 300)"

# A stable, per-VM hostname keeps runner names distinct across clones.
log "$vm: setting hostname"
for key in ComputerName HostName LocalHostName; do
	tart exec "$vm" sudo scutil --set "$key" "$vm"
done

log "$vm: writing $agent registration secret"
push_registration_secret

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
