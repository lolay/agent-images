#!/bin/bash
# Starts this VM's runner, and restarts it whenever it exits for as long as
# ~/.config/agent-runner/runner.env exists. On macOS, launchd runs it in the
# agent user's GUI session (Simulator needs one) through `zsh -l`, so PATH comes
# from ~/.zprofile (KeepAlive PathState). On Linux, systemd's agent-runner.path
# starts agent-runner.service through `bash -l` (PATH from /etc/profile.d).
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
warn() { log "$@" >&2; }

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
	warn "unknown AGENT=$AGENT (no $runner_command)"
	exit 1
fi

export AGENT_RUNNER_CONFIG_DIR="$config_dir"
log "starting $AGENT runner as ${RUNNER_LABEL:-$(hostname -s)}"

if [[ "${EPHEMERAL:-false}" == "true" ]]; then
	# Not exec: this script powers the VM off afterwards. launchd signals only
	# this process, so pass SIGTERM on and let the runner drain and push its
	# session's work before exiting.
	"$runner_command" &
	runner_pid=$!
	trap 'kill -TERM "$runner_pid" 2>/dev/null' TERM INT
	# A trapped signal interrupts wait with 128+signal; wait again until the
	# runner itself exits, so status is its own.
	while :; do
		if wait "$runner_pid"; then status=0; else status=$?; fi
		kill -0 "$runner_pid" 2>/dev/null || break
	done
	log "$AGENT runner exited ($status); shutting down"
	exec sudo /sbin/shutdown -h now
fi
exec "$runner_command"
