#!/bin/bash
# Runs one ephemeral runner VM for one orchestrator work order: launch it from
# the image, configure it with the work order as its registration secret, wait
# for it to power off after its session (LXD deletes an ephemeral VM when it
# stops), then release its name. The Linux counterpart of scripts/runner-once.sh.
#
# Usage: scripts/linux/runner-once.sh <vm>
#
# hooks/linux/spawn-runner claims the name and submits this as systemd user unit
# agent-images-<vm>.service, so it outlives the hook. SIGTERM (make runner-stop,
# or runner-uninstall) stops and deletes the VM; its session requeues.
#
# The name is released only once LXD says the VM is gone. While LXD doesn't
# answer (a snap refresh restarts it) this waits, up to LXD_DOWN_LIMIT_MIN
# (default 1440, three times a session's 480-minute cap), then gives up with an
# error and leaves the claim to reclaim_stale_runners.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/linux/lib.sh
source "$script_dir/lib.sh"

readonly vm="${1:?usage: runner-once.sh <vm>}"
is_runner_name "$vm" || die "$vm is not a runner name (runner-N)"
readonly runner_dir="$RUNNERS_DIR/$vm"
readonly work_order="$runner_dir/work-order"
image_ref="$(setting IMAGE_REF "$ANDROID_IMAGE_ALIAS")"
cpu="$(setting VM_CPU 6)"
memory_gb="$(setting VM_MEMORY_GB 24)"
lxd_down_limit_min="$(setting LXD_DOWN_LIMIT_MIN 1440)"
readonly image_ref cpu memory_gb lxd_down_limit_min

[[ -d "$runner_dir" ]] || die "$vm: not claimed (no $runner_dir)"
printf '%s\n' "$$" >"$runner_dir/pid"
mkdir -p "$AGENT_IMAGES_LOG_DIR"

guest_log_pid=""

cleanup() {
	local listed=0
	rm -f "$work_order"
	vm_listed_patiently "$vm" || listed=$?
	# Stopped early (SIGTERM): a clean shutdown lets the runner drain and push its
	# session's work before the VM goes.
	if ((listed == 0)); then
		lxc stop --timeout "$VM_STOP_TIMEOUT_SEC" "$vm" >/dev/null 2>&1 || true
	fi
	if [[ -n "$guest_log_pid" ]]; then
		kill "$guest_log_pid" 2>/dev/null || true
	fi
	if ((listed != 1)); then
		listed=0
		vm_listed_patiently "$vm" || listed=$?
	fi
	if ((listed == 0)); then
		lxc delete --force "$vm" >/dev/null 2>&1 || true
		listed=0
		vm_listed_patiently "$vm" || listed=$?
	fi
	if ((listed != 1)); then
		log "$vm: not deleted (LXD isn't answering, or the delete failed); keeping its claim for reclaim" >&2
		return
	fi
	rm -rf "$runner_dir"
	log "$vm: deleted"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# The guest's journal is deleted with the VM, so copy the runner's and
# watchdog's output to the host as it's written, with a header per VM. It ends
# when the VM powers off.
stream_guest_logs() {
	printf '\n=== %s %s order %s ===\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$vm" \
		"$(cat "$runner_dir/order-id" 2>/dev/null || echo unknown)" >>"$AGENT_IMAGES_LOG_DIR/$vm.guest.log"
	lxc exec "$vm" -- journalctl --follow --output=short-iso --no-tail \
		--unit agent-runner.service --unit agent-runner-watchdog.service \
		>>"$AGENT_IMAGES_LOG_DIR/$vm.guest.log" 2>/dev/null &
	guest_log_pid=$!
}

log "$vm: launching $image_ref"
lxc launch "$image_ref" "$vm" --vm --ephemeral \
	--config limits.cpu="$cpu" --config limits.memory="${memory_gb}GiB" \
	>>"$AGENT_IMAGES_LOG_DIR/$vm.lxc.log" 2>&1

if EPHEMERAL=true ENVIRONMENT_SECRET_FILE="$work_order" \
	"$script_dir/vm-configure.sh" --config "$RUNNER_CONFIG" "$vm"; then
	# Spent once the runner registers; no reason to keep it on disk.
	rm -f "$work_order"
	stream_guest_logs
	log "$vm: waiting for its session to finish"
	# Only a VM that LXD no longer lists has powered off. A failed lxc (LXD
	# restarting) ends nothing: stopping on it would cut a live session short.
	down_sec=0
	while :; do
		listed=0
		vm_listed "$vm" || listed=$?
		case "$listed" in
		0)
			((down_sec == 0)) || log "$vm: LXD answers again"
			down_sec=0
			;;
		1) break ;;
		*)
			((down_sec > 0)) || log "$vm: LXD isn't answering; still waiting" >&2
			((down_sec < lxd_down_limit_min * 60)) ||
				die "$vm: LXD hasn't answered for $lxd_down_limit_min minutes; giving up (the claim stays for reclaim)"
			down_sec=$((down_sec + 10))
			;;
		esac
		sleep 10
	done
else
	log "$vm: configure failed (see $AGENT_IMAGES_LOG_DIR/$vm.runner.log)" >&2
fi
