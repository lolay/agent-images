#!/bin/bash
# Starts this VM's runner. launchd runs it in the agent user's GUI session
# (Simulator needs one) through `zsh -l`, so PATH comes from ~/.zprofile, and
# restarts it whenever it exits, for as long as ~/.config/agent-runner/runner.env
# exists (KeepAlive PathState).
#
# With EPHEMERAL=true (set by the host's runner loop), the VM serves one
# session: when the runner exits, it powers off and the host deletes it.
#
# runner.env is written by `make vm-configure` on the host. It selects the
# agent (AGENT=claude, the only one today) and holds non-secret settings; secrets live in
# separate owner-only files the runner scripts read directly.
set -euo pipefail

readonly config_dir="$HOME/.config/agent-runner"
readonly runner_env="$config_dir/runner.env"

log() { printf '%s agent-runner: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

if [[ ! -f "$runner_env" ]]; then
	# Not configured yet. Exiting is fine: launchd starts us once the file appears.
	log "no $runner_env; waiting for make vm-configure"
	exit 0
fi

set -a
# shellcheck source=/dev/null
source "$runner_env"
set +a

: "${AGENT:?AGENT is not set in $runner_env}"
readonly runner_command="$HOME/bin/agent-runner-$AGENT"
if [[ ! -x "$runner_command" ]]; then
	log "unknown AGENT=$AGENT (no $runner_command)"
	exit 1
fi

export AGENT_RUNNER_CONFIG_DIR="$config_dir"
log "starting $AGENT runner as ${RUNNER_LABEL:-$(hostname -s)}"

if [[ "${EPHEMERAL:-false}" == "true" ]]; then
	status=0
	"$runner_command" || status=$?
	log "$AGENT runner exited ($status); shutting down"
	exec sudo /sbin/shutdown -h now
fi
exec "$runner_command"
