#!/bin/bash
# Runs one ephemeral runner: clone a fresh VM from the image, boot it, configure
# it, let it serve one session, then delete it and start over. Runs until
# stopped; on SIGINT/SIGTERM it stops and deletes the VM it owns.
#
# Usage: scripts/runner-run.sh <vm>
#
# The VM name is the runner's identity (hostname and runner label), so the same
# name comes back on every cycle. The loop owns that name: it refuses to start if
# a VM by that name already exists, and deletes its own VM after each session.
#
# Settings: IMAGE_REF, VM_CPU, VM_MEMORY_GB from the environment, else from
# .env. The host LaunchAgent runs this script directly rather than through make,
# so the signal that stops it reaches this script and the VM gets cleaned up.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

readonly vm="${1:?usage: runner-run.sh <vm>}"
readonly repo_dir="$script_dir/.."
readonly config="$repo_dir/vms/$vm.env"
readonly dotenv="$repo_dir/.env"

# Prints a setting from the environment, else .env, else the default.
setting() {
	local key="$1" default="$2" value="${!1:-}"
	if [[ -z "$value" && -f "$dotenv" ]]; then
		value="$(env_value "$dotenv" "$key")"
	fi
	printf '%s' "${value:-$default}"
}

image_ref="$(setting IMAGE_REF agent-macos)"
cpu="$(setting VM_CPU 4)"
memory_gb="$(setting VM_MEMORY_GB 12)"
readonly image_ref cpu memory_gb
readonly log_dir="$repo_dir/build/logs"
# A VM that dies faster than this probably hit a configuration error (a bad
# secret, a missing image); waiting keeps the loop from spinning.
readonly min_lifetime_seconds=60
readonly backoff_seconds=60

[[ -f "$config" ]] || die "no $config; copy one of vms/example-*.env"

vm_exists() { tart get "$vm" >/dev/null 2>&1; }

tart_pid=""

delete_vm() {
	if [[ -n "$tart_pid" ]] && kill -0 "$tart_pid" 2>/dev/null; then
		tart stop "$vm" >/dev/null 2>&1 || true
		wait "$tart_pid" 2>/dev/null || true
	fi
	tart_pid=""
	if vm_exists; then
		tart delete "$vm"
	fi
}

on_exit() {
	log "$vm: stopping; deleting VM"
	delete_vm
}

vm_exists && die "a VM named $vm already exists; delete it (make vm-delete VM=$vm) or use another name"

trap on_exit EXIT
trap 'exit 130' INT TERM

mkdir -p "$log_dir"

while true; do
	log "$vm: cloning $image_ref"
	tart clone "$image_ref" "$vm"
	tart set "$vm" --cpu "$cpu" --memory "$((memory_gb * 1024))"

	started_at="$(date +%s)"
	tart run --no-graphics "$vm" >>"$log_dir/$vm.log" 2>&1 &
	tart_pid=$!

	if EPHEMERAL=true "$script_dir/vm-configure.sh" "$vm"; then
		log "$vm: waiting for the session to finish"
		wait "$tart_pid" || true
		tart_pid=""
	else
		log "$vm: configure failed"
	fi

	delete_vm
	lifetime=$(($(date +%s) - started_at))
	log "$vm: VM deleted after ${lifetime}s"

	if ((lifetime < min_lifetime_seconds)); then
		log "$vm: VM lived under ${min_lifetime_seconds}s; waiting ${backoff_seconds}s (see $log_dir/$vm.log)"
		# Backgrounded so a stop signal isn't held until the sleep ends.
		sleep "$backoff_seconds" &
		wait $!
	fi
done
