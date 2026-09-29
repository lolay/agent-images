#!/bin/bash
# Runs one ephemeral runner VM for one orchestrator work order: clone the image,
# boot it, configure it with the work order as its registration secret, wait for
# it to power off after its session, then delete it and release its name.
#
# Usage: scripts/runner-once.sh <vm>
#
# hooks/spawn-runner claims the name and submits this as launchd job
# com.agent-images.<vm>, so it outlives the hook. SIGTERM (make runner-stop, or
# runner-uninstall) stops and deletes the VM; its session requeues.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

readonly vm="${1:?usage: runner-once.sh <vm>}"
is_runner_name "$vm" || die "$vm is not a runner name (runner-N)"
readonly runner_dir="$RUNNERS_DIR/$vm"
readonly work_order="$runner_dir/work-order"
image_ref="$(setting IMAGE_REF agent-macos)"
cpu="$(setting VM_CPU 4)"
memory_gb="$(setting VM_MEMORY_GB 12)"
readonly image_ref cpu memory_gb

[[ -d "$runner_dir" ]] || die "$vm: not claimed (no $runner_dir)"
printf '%s\n' "$$" >"$runner_dir/pid"
mkdir -p "$AGENT_IMAGES_LOG_DIR"

tart_pid=""
guest_log_pids=()

cleanup() {
	rm -f "$work_order"
	if ((${#guest_log_pids[@]} > 0)); then
		kill "${guest_log_pids[@]}" 2>/dev/null || true
	fi
	if [[ -n "$tart_pid" ]] && kill -0 "$tart_pid" 2>/dev/null; then
		tart stop "$vm" >/dev/null 2>&1 || true
		wait "$tart_pid" 2>/dev/null || true
	fi
	if vm_exists "$vm"; then
		tart delete "$vm"
	fi
	rm -rf "$runner_dir"
	log "$vm: deleted"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# The guest's logs are deleted with the VM, so copy them to the host as they're
# written: the runner's and watchdog's stdout to build/logs/<vm>.guest.out and
# their stderr to <vm>.guest.err, with a header per VM. tail ends when the VM
# powers off. tart exec runs as the agent user, who owns these logs.
stream_guest_logs() {
	local guest_logs="/Users/$AGENT_USER/Library/Logs" header stream
	header="$(printf '=== %s %s order %s ===' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$vm" \
		"$(cat "$runner_dir/order-id" 2>/dev/null || echo unknown)")"
	for stream in out err; do
		printf '\n%s\n' "$header" >>"$AGENT_IMAGES_LOG_DIR/$vm.guest.$stream"
		tart exec "$vm" tail -n +1 -F \
			"$guest_logs/agent-runner.$stream" "$guest_logs/agent-runner-watchdog.$stream" \
			>>"$AGENT_IMAGES_LOG_DIR/$vm.guest.$stream" 2>/dev/null &
		guest_log_pids+=($!)
	done
}

log "$vm: cloning $image_ref"
tart clone "$image_ref" "$vm"
tart set "$vm" --cpu "$cpu" --memory "$((memory_gb * 1024))"

tart run --no-graphics "$vm" >>"$AGENT_IMAGES_LOG_DIR/$vm.tart.out" 2>>"$AGENT_IMAGES_LOG_DIR/$vm.tart.err" &
tart_pid=$!

if EPHEMERAL=true ENVIRONMENT_SECRET_FILE="$work_order" \
	"$script_dir/vm-configure.sh" --config "$RUNNER_CONFIG" "$vm"; then
	# Spent once the runner registers; no reason to keep it on disk.
	rm -f "$work_order"
	stream_guest_logs
	log "$vm: waiting for its session to finish"
	wait "$tart_pid" || true
	tart_pid=""
else
	log "$vm: configure failed (see $AGENT_IMAGES_LOG_DIR/$vm.runner.err)" >&2
fi
