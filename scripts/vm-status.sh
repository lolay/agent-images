#!/bin/bash
# Reports each VM's runner: configured agent, whether its process is up,
# Claude's /healthz, and the last log lines. Read-only.
#
# Usage: scripts/vm-status.sh <vm>...
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

readonly log_lines="${LOG_LINES:-5}"
readonly claude_health_url="http://127.0.0.1:8080/healthz"

[[ $# -gt 0 ]] || die "usage: vm-status.sh <vm>..."

guest_home="/Users/$AGENT_USER"

for vm in "$@"; do
	printf '\n%s\n' "$vm"
	if ! tart exec "$vm" true >/dev/null 2>&1; then
		printf '  state    not running (or guest agent unreachable)\n'
		continue
	fi

	agent="$(tart exec "$vm" sudo sed -n -E 's/^AGENT=(.*)$/\1/p' \
		"$guest_home/.config/agent-runner/runner.env" 2>/dev/null || true)"
	printf '  agent    %s\n' "${agent:-not configured}"

	if tart exec "$vm" pgrep -u "$AGENT_USER" -f 'self-hosted-runner|cursor-agent worker' >/dev/null 2>&1; then
		printf '  process  up\n'
	else
		printf '  process  down\n'
	fi

	if [[ "$agent" == "claude" ]]; then
		health="$(tart exec "$vm" curl -fsS --max-time 5 "$claude_health_url" 2>/dev/null || true)"
		printf '  healthz  %s\n' "${health:-no response}"
	fi

	printf '  log\n'
	tart exec "$vm" sudo tail -n "$log_lines" "$guest_home/Library/Logs/agent-runner.log" 2>/dev/null |
		sed 's/^/    /' || printf '    (no log yet)\n'
done
