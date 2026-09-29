#!/bin/bash
# Installs, removes, or reports the host LaunchAgent that keeps the orchestrator
# (orchestrator-run.sh) running, and stops runner VMs.
#
# Usage: scripts/orchestrator-service.sh install|uninstall|stop-runners|status
#
# The LaunchAgent starts at the host user's login. Turn on automatic login for
# that user so runners come back after a host restart. Runner VMs run as their
# own launchd jobs, so restarting the orchestrator leaves them serving.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

readonly action="${1:?usage: orchestrator-service.sh install|uninstall|stop-runners|status}"
readonly label="com.agent-images.orchestrator"
readonly plist="$HOME/Library/LaunchAgents/$label.plist"
readonly log_file="$AGENT_IMAGES_LOG_DIR/orchestrator.log"
domain="$(launchd_domain)"
readonly domain

is_loaded() { launchctl print "$domain/$1" >/dev/null 2>&1; }

runner_names() {
	local runner_dir
	for runner_dir in "$RUNNERS_DIR"/runner-*/; do
		[[ -d "$runner_dir" ]] && basename "$runner_dir"
	done
	return 0
}

# SIGTERM each runner job; runner-once stops and deletes its VM, and any session
# on it requeues. bootout waits for the job, up to its ExitTimeOut.
stop_runners() {
	local vm
	for vm in $(runner_names); do
		log "$vm: stopping"
		launchctl bootout "$domain/$(runner_job_label "$vm")" >/dev/null 2>&1 || true
	done
	reclaim_stale_runners
}

case "$action" in
install)
	command -v claude >/dev/null 2>&1 || die "claude not found; brew install --cask claude-code@latest"
	[[ -f "$RUNNER_CONFIG" ]] || die "no $RUNNER_CONFIG; cp vms/example-claude.env vms/runner.env"
	keychain_secret claude-environment-secret orchestrator >/dev/null ||
		die "no Keychain item agent-images.claude-environment-secret; run make secret-set NAME=claude-environment-secret"
	is_loaded "$label" && die "$label is already installed; run make runner-uninstall first"
	mkdir -p "$(dirname "$plist")" "$AGENT_IMAGES_LOG_DIR"
	write_host_plist "$plist" "$label" "$log_file" true \
		/bin/bash "$AGENT_IMAGES_DIR/scripts/orchestrator-run.sh"
	launchctl bootstrap "$domain" "$plist"
	log "installed $label (log: $log_file)"
	;;
uninstall)
	if is_loaded "$label"; then
		launchctl bootout "$domain/$label"
	fi
	rm -f "$plist"
	stop_runners
	log "uninstalled $label"
	;;
stop-runners)
	stop_runners
	;;
status)
	if is_loaded "$label"; then
		state="$(launchctl print "$domain/$label" | sed -n -E 's/^[[:space:]]*state = (.*)$/\1/p' | head -n 1)"
		printf 'orchestrator  %s (%s)\n' "$label" "${state:-loaded}"
		health="$(curl -fsS --max-time 3 "http://127.0.0.1:$(setting ORCHESTRATOR_HEALTH_PORT 8080)/healthz" 2>/dev/null || true)"
		printf '  healthz     %s\n' "${health:-no response}"
	else
		printf 'orchestrator  not installed\n'
	fi
	if [[ -f "$log_file" ]]; then
		printf '  log\n'
		tail -n "${LOG_LINES:-5}" "$log_file" | sed 's/^/    /'
	fi
	names="$(runner_names)"
	if [[ -z "$names" ]]; then
		printf '\nno runner VMs\n'
		exit 0
	fi
	# shellcheck disable=SC2086 # one argument per name
	"$script_dir/vm-status.sh" $names
	;;
*)
	die "unknown action $action (install, uninstall, stop-runners, or status)"
	;;
esac
