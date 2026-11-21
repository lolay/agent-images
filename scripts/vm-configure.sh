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
# single-use work order), else the VM's own Keychain item
# agent-images.claude-environment-secret, account <vm> (make secret-set
# NAME=claude-environment-secret VM=<vm>): a separate environment's key. Never
# the orchestrator's (account orchestrator or default) or the host runner's
# (host, host-<context>), which any session on the VM could read and reuse: those
# names are refused as VM names, and a copy of the orchestrator's key too.
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

# Read before anything touches the guest, so a VM with no secret of its own is
# left as it was.
vm_secret=""
if [[ -n "${ENVIRONMENT_SECRET_FILE:-}" ]]; then
	[[ -s "$ENVIRONMENT_SECRET_FILE" ]] || die "$ENVIRONMENT_SECRET_FILE is missing or empty"
else
	is_host_secret_account "$vm" &&
		die "$vm names the Keychain account of a host key (orchestrator, default, host, host-*); name the VM something else"
	status=0
	vm_secret="$(keychain_secret claude-environment-secret "$vm")" || status=$?
	# 1: keychain_secret has said why (a locked keychain); 2: no item for this VM.
	((status != 1)) || exit 1
	((status == 0)) || die "$vm takes a work order or its own environment key, never the orchestrator's"
	orchestrator_secret="$(keychain_secret claude-environment-secret orchestrator default 2>/dev/null)" || true
	[[ "$vm_secret" != "$orchestrator_secret" ]] ||
		die "$vm's Keychain item is the orchestrator's environment key; give $vm a separate environment's key: make secret-set NAME=claude-environment-secret VM=$vm"
fi
readonly vm_secret

push_registration_secret() {
	local guest_path=".claude-runner/environment-secret"
	if [[ -n "${ENVIRONMENT_SECRET_FILE:-}" ]]; then
		guest_write "$vm" "$guest_path" <"$ENVIRONMENT_SECRET_FILE"
	else
		printf '%s' "$vm_secret" | guest_write "$vm" "$guest_path"
	fi
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
