#!/bin/bash
# Shared helpers for the Linux host's scripts: LXD for VMs, systemd user units
# for jobs, and an owner-only file for the environment secret. Sourced, not
# executed. Sources scripts/lib.sh for the portable helpers (log, die, setting,
# env_value, is_runner_name, paths) and replaces its Tart, launchd, and Keychain
# functions with these.

# The base image's own user, which runs the runner: the Ubuntu cloud image's
# ubuntu (cloud-init gives it passwordless sudo), as Cirrus's admin is on macOS.
# Set before sourcing lib.sh, which defaults it to admin.
GUEST_USER="${GUEST_USER:-ubuntu}"

# shellcheck source=scripts/lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib.sh"

# Where the Keychain would be: owner-only, on this dedicated host.
readonly SECRETS_DIR="$HOME/.config/agent-images"

# The LXD image runner VMs launch from (make build).
# shellcheck disable=SC2034 # used by the scripts that source this
readonly ANDROID_IMAGE_ALIAS="agent-android"

# Prints a secret: the VM-specific file if present, else the default one.
# Returns 2 if neither exists (or both are empty), and 1 if the first present
# one can't be read (a mode, or a directory): that isn't a reason to fall back
# to the other, a different key. The same statuses as keychain_secret.
host_secret() {
	local name="$1" target_vm="$2" file
	for file in "$SECRETS_DIR/$name.$target_vm" "$SECRETS_DIR/$name"; do
		[[ -s "$file" ]] || continue
		cat "$file" || return 1
		return 0
	done
	return 2
}

vm_exists() { lxc info "$1" >/dev/null 2>&1; }

# Like vm_exists, but tells a VM that's gone apart from an lxc that failed: 0
# listed, 1 gone, 2 LXD didn't answer (a snap refresh restarts it), which says
# nothing about the VM. lxc info fails the same way for both.
vm_listed() {
	local names
	names="$(lxc list --format csv --columns n 2>/dev/null)" || return 2
	grep -Fxq -- "$1" <<<"$names"
}

# vm_listed, asked again for about half a minute while LXD doesn't answer: a
# snap refresh restarts it. Same statuses; 2 means it still didn't answer.
vm_listed_patiently() {
	local attempt listed
	for attempt in 1 2 3 4 5 6; do
		listed=0
		vm_listed "$1" || listed=$?
		((listed == 2)) || return "$listed"
		((attempt == 6)) || sleep 5
	done
	return 2
}

# Waits until lxd-agent answers, so lxc exec works.
wait_for_guest() {
	local target_vm="$1" timeout="${2:-180}" waited=0
	until lxc exec "$target_vm" -- true >/dev/null 2>&1; do
		((waited >= timeout)) && die "$target_vm: lxd-agent not reachable after ${timeout}s (is the VM running?)"
		sleep 5
		waited=$((waited + 5))
	done
}

# Runs a command in the guest as the guest user, the way tart exec does on macOS.
# lxc exec runs as root, so this goes through sudo; stdin passes through.
# Usage: guest_exec <vm> <command>...
guest_exec() {
	local target_vm="$1"
	shift
	lxc exec "$target_vm" --force-noninteractive -- sudo -u "$GUEST_USER" -H "$@"
}

# Writes stdin to a path under the guest user's home, owner-only, like the
# macOS guest_write: over stdin so secrets never appear in a process list, and
# renamed into place so systemd never starts the runner on half a runner.env.
guest_write() {
	local target_vm="$1" relative_path="$2"
	# shellcheck disable=SC2016 # expanded by the guest shell, not here
	guest_exec "$target_vm" /bin/bash -c \
		'umask 077; target="$HOME/$1"; mkdir -p "$(dirname "$target")"; cat >"$target.partial" && mv -f "$target.partial" "$target"' \
		_ "$relative_path"
}

runner_unit() { printf 'agent-images-%s.service' "$1"; }

# Deletes runner VMs whose runner-once unit is gone (a host crash, a killed
# unit) and releases their names. Same contract as the macOS version. A name is
# released only once LXD says its VM is gone: while LXD doesn't answer, the claim
# stays for the next reclaim.
# Usage: reclaim_stale_runners [grace-minutes] (see stale_runner_claims)
reclaim_stale_runners() {
	local grace_minutes="${1:-2}" claim_vm listed
	for claim_vm in $(stale_runner_claims "$grace_minutes"); do
		log "$claim_vm: reclaiming (its runner job is gone)"
		systemctl --user stop "$(runner_unit "$claim_vm")" >/dev/null 2>&1 || true
		listed=0
		vm_listed_patiently "$claim_vm" || listed=$?
		if ((listed == 0)); then
			lxc delete --force "$claim_vm"
		elif ((listed == 2)); then
			log "$claim_vm: LXD isn't answering; keeping the claim for the next reclaim" >&2
			continue
		fi
		rm -rf "${RUNNERS_DIR:?}/$claim_vm"
	done
}
