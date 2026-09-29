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
readonly runner_agent="com.agent-images.runner"
# The runner's label in the Anthropic console, and the guest's hostname:
# <host>-<vm>, so runner-1 on two hosts stays distinguishable.
host_name="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
runner_label="$(setting RUNNER_LABEL_PREFIX "$host_name")-$vm"
readonly runner_label

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

log "$vm: waiting for the $GUEST_USER session"
wait_for_guest "$vm" "$(setting VM_BOOT_TIMEOUT 300)"

log "$vm: setting hostname to $runner_label"
for key in ComputerName HostName LocalHostName; do
	tart exec "$vm" sudo -n /usr/sbin/scutil --set "$key" "$runner_label"
done

log "$vm: writing $agent registration secret"
push_registration_secret

# Written last: its presence is what tells launchd to keep the runner running.
log "$vm: writing runner.env"
{
	cat "$config"
	printf '\nRUNNER_LABEL=%s\n' "$runner_label"
	if [[ "${EPHEMERAL:-false}" == "true" ]]; then
		printf 'EPHEMERAL=true\n'
	fi
} | guest_write "$vm" .config/agent-runner/runner.env

if [[ "${EPHEMERAL:-false}" == "true" ]]; then
	# A fresh VM's runner starts when runner.env appears (KeepAlive PathState).
	# Restarting it would register a second time, and a work order is single-use.
	log "$vm: runner starts on runner.env"
else
	agent_uid="$(tart exec "$vm" id -u)"
	log "$vm: restarting runner"
	tart exec "$vm" launchctl kickstart -k "gui/$agent_uid/$runner_agent"
fi
log "$vm: configured as $agent ($runner_label)"
