#!/bin/bash
# Runs this VM's Android emulator: AVD "agent" (built by ensure-android-sdk.sh),
# headless, KVM-accelerated, as emulator-5554. Installed as ~/bin/agent-emulator.
# agent-runner-claude starts it at every runner start, so it's booted before a
# session needs it; the image build uses it to save the quickboot snapshot.
#
# Usage: agent-emulator start|wait|stop|status
#   start   Boot in the background, unless it's already running. Quick-boots
#           from the image's snapshot and saves nothing on exit, so every boot
#           starts clean (AGENT_EMULATOR_SAVE_SNAPSHOT=1 saves one; the build).
#   wait    Block until Android has finished booting (AGENT_EMULATOR_TIMEOUT
#           seconds, default 300).
#   stop    Shut it down and wait for it to exit.
#   status  Whether it runs, its adb state, and KVM acceleration.
#
# AGENT_EMULATOR_ARGS adds emulator flags to start (space-separated).
#
# Sessions own it: stop it and run `emulator` with other flags or AVDs as needed.
set -euo pipefail

readonly avd="${AGENT_EMULATOR_AVD:-agent}"
readonly port=5554
readonly serial="emulator-$port"
readonly timeout="${AGENT_EMULATOR_TIMEOUT:-300}"
readonly log_dir="${XDG_STATE_HOME:-$HOME/.local/state}"
readonly log_file="$log_dir/agent-emulator.log"

log() { printf '%s agent-emulator: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() {
	log "$*" >&2
	exit 1
}

# The emulator's main process carries the AVD name on its command line.
emulator_pids() { pgrep -u "$(id -u)" -f -- "-avd $avd( |$)" || true; }

booted() { [[ "$(adb -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; }

start() {
	if [[ -n "$(emulator_pids)" ]]; then
		log "$avd is already running"
		return 0
	fi
	[[ -r /dev/kvm && -w /dev/kvm ]] ||
		die "no usable /dev/kvm; the emulator needs KVM (nested virtualization on the host, and $(id -un) in the kvm group)"
	command -v emulator >/dev/null 2>&1 || die "emulator not on PATH (is ANDROID_HOME set?)"

	# -no-metrics: the emulator's metrics notice is due to become a blocking prompt.
	local args=(-avd "$avd" -port "$port" -no-window -no-audio -no-boot-anim -no-metrics -gpu swiftshader -accel on)
	if [[ "${AGENT_EMULATOR_SAVE_SNAPSHOT:-0}" != "1" ]]; then
		args+=(-no-snapshot-save)
	fi
	# AGENT_EMULATOR_ARGS: extra emulator flags, space-separated.
	local extra=()
	read -r -a extra <<<"${AGENT_EMULATOR_ARGS:-}"
	args+=("${extra[@]}")
	mkdir -p "$log_dir"
	printf '\n=== %s emulator %s ===\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${args[*]}" >>"$log_file"
	# Detached from this shell, so it outlives the caller (the runner's start).
	setsid nohup emulator "${args[@]}" >>"$log_file" 2>&1 </dev/null &
	log "started $avd as $serial (log: $log_file)"
}

wait_booted() {
	local deadline=$((SECONDS + timeout))
	adb start-server >/dev/null 2>&1 || true
	until booted; do
		if [[ -z "$(emulator_pids)" ]]; then
			tail -n 20 "$log_file" >&2 2>/dev/null || true
			die "$avd isn't running (see $log_file)"
		fi
		((SECONDS < deadline)) || die "$avd didn't finish booting within ${timeout}s (see $log_file)"
		sleep 3
	done
	log "$serial booted (Android $(adb -s "$serial" shell getprop ro.build.version.release | tr -d '\r'))"
}

stop() {
	local waited=0
	if [[ -z "$(emulator_pids)" ]]; then
		log "$avd isn't running"
		return 0
	fi
	adb -s "$serial" emu kill >/dev/null 2>&1 || true
	while [[ -n "$(emulator_pids)" ]]; do
		if ((waited >= 60)); then
			log "$avd didn't exit; killing it"
			# shellcheck disable=SC2046 # one argument per PID
			kill $(emulator_pids) 2>/dev/null || true
			break
		fi
		sleep 2
		waited=$((waited + 2))
	done
	log "stopped $avd"
}

status() {
	if [[ -n "$(emulator_pids)" ]]; then
		printf 'emulator  running (%s)\n' "$avd"
	else
		printf 'emulator  not running\n'
	fi
	if booted; then
		printf 'adb       %s booted\n' "$serial"
	else
		printf 'adb       %s not booted\n' "$serial"
	fi
	# -accel-check prints "accel:", a status code, the verdict, then "accel".
	printf 'accel     %s\n' "$(emulator -accel-check 2>&1 | grep -vE '^(accel:?|[0-9]+)$' | head -n 1)"
}

case "${1:-}" in
start) start ;;
wait) wait_booted ;;
stop) stop ;;
status) status ;;
*) die "usage: agent-emulator start|wait|stop|status" ;;
esac
