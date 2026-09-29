#!/bin/bash
# Installs, removes, or reports the systemd user unit that keeps the orchestrator
# (orchestrator-run.sh) running, and stops runner VMs. The Linux counterpart of
# scripts/orchestrator-service.sh.
#
# Usage: scripts/linux/orchestrator-service.sh install|uninstall|stop-runners|status
#
# With lingering on (make host-setup), the user's systemd manager starts at boot,
# so the orchestrator comes back after a restart with nobody logged in. Runner
# VMs run as their own units, so restarting the orchestrator leaves them serving.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/linux/lib.sh
source "$script_dir/lib.sh"

readonly action="${1:?usage: orchestrator-service.sh install|uninstall|stop-runners|status}"
readonly unit="agent-images-orchestrator.service"
readonly unit_file="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$unit"
readonly log_file="$AGENT_IMAGES_LOG_DIR/orchestrator.log"
readonly claude_health_url="http://127.0.0.1:8080/healthz"

runner_names() {
	local runner_dir
	for runner_dir in "$RUNNERS_DIR"/runner-*/; do
		[[ -d "$runner_dir" ]] && basename "$runner_dir"
	done
	return 0
}

# SIGTERM each runner unit; runner-once stops and deletes its VM, and any session
# on it requeues. systemctl stop waits for the unit, up to its TimeoutStopSec.
stop_runners() {
	local vm
	for vm in $(runner_names); do
		log "$vm: stopping"
		systemctl --user stop "$(runner_unit "$vm")" >/dev/null 2>&1 || true
	done
	reclaim_stale_runners
}

vm_status() {
	local vm="$1" health
	printf '\n%s\n' "$vm"
	if ! lxc exec "$vm" -- true >/dev/null 2>&1; then
		printf '  state    not running (or lxd-agent unreachable)\n'
		return
	fi
	if lxc exec "$vm" -- pgrep -u "$GUEST_USER" -f 'self-hosted-runner' >/dev/null 2>&1; then
		printf '  process  up\n'
	else
		printf '  process  down\n'
	fi
	health="$(lxc exec "$vm" -- curl -fsS --max-time 5 "$claude_health_url" 2>/dev/null || true)"
	printf '  healthz  %s\n' "${health:-no response}"
	printf '  %s\n' "$(guest_exec "$vm" bash -lc 'agent-emulator status' 2>/dev/null |
		head -n 1 || echo 'emulator  unknown')"
}

case "$action" in
install)
	command -v claude >/dev/null 2>&1 || die "claude not found; run make host-setup"
	[[ -f "$RUNNER_CONFIG" ]] || die "no $RUNNER_CONFIG; cp vms/example-linux.env vms/runner.env"
	host_secret claude-environment-secret orchestrator >/dev/null ||
		die "no $SECRETS_DIR/claude-environment-secret; run make secret-set NAME=claude-environment-secret"
	systemctl --user is-enabled "$unit" >/dev/null 2>&1 && die "$unit is already installed; run make runner-uninstall first"
	mkdir -p "$(dirname "$unit_file")" "$AGENT_IMAGES_LOG_DIR"
	# PATH carries lxc (/snap/bin) and claude (~/.local/bin) for the orchestrator,
	# its hook, and the runner units the hook submits.
	cat >"$unit_file" <<EOF
[Unit]
Description=agent-images orchestrator (claude self-hosted-runner orchestrator)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$AGENT_IMAGES_DIR
Environment=PATH=$HOME/.local/bin:/snap/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash $AGENT_IMAGES_DIR/scripts/linux/orchestrator-run.sh
Restart=always
RestartSec=30
TimeoutStopSec=120
StandardOutput=append:$log_file
StandardError=append:$log_file

[Install]
WantedBy=default.target
EOF
	systemctl --user daemon-reload
	systemctl --user enable --now "$unit"
	log "installed $unit (log: $log_file)"
	if [[ "$(loginctl show-user "$(id -un)" --property=Linger --value 2>/dev/null)" != "yes" ]]; then
		log "lingering is off, so the orchestrator stops at logout; run make host-setup" >&2
	fi
	;;
uninstall)
	systemctl --user disable --now "$unit" >/dev/null 2>&1 || true
	rm -f "$unit_file"
	systemctl --user daemon-reload
	stop_runners
	log "uninstalled $unit"
	;;
stop-runners)
	stop_runners
	;;
status)
	if systemctl --user is-enabled "$unit" >/dev/null 2>&1; then
		printf 'orchestrator  %s (%s)\n' "$unit" "$(systemctl --user is-active "$unit" 2>/dev/null || true)"
		health="$(curl -fsS --max-time 3 "http://127.0.0.1:$(setting ORCHESTRATOR_HEALTH_PORT 8080)/healthz" 2>/dev/null || true)"
		printf '  healthz     %s\n' "${health:-no response}"
	else
		printf 'orchestrator  not installed\n'
	fi
	if [[ -s "$log_file" ]]; then
		printf '  %s\n' "$(basename "$log_file")"
		tail -n "${LOG_LINES:-5}" "$log_file" | sed 's/^/    /'
	fi
	names="$(runner_names)"
	if [[ -z "$names" ]]; then
		printf '\nno runner VMs\n'
		exit 0
	fi
	for vm in $names; do
		vm_status "$vm"
	done
	;;
*)
	die "unknown action $action (install, uninstall, stop-runners, or status)"
	;;
esac
