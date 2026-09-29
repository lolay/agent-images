#!/bin/bash
# Runs `claude self-hosted-runner orchestrator` for this host. It watches the
# self-hosted environment's queue and runs hooks/spawn-runner for each session
# that needs a runner, plus enough standby runners to keep RUNNER_MIN_IDLE free.
# The environment secret comes from the Keychain and stays on this host; each
# runner VM gets a single-use work order instead.
#
# Usage: scripts/orchestrator-run.sh  (make runner-run, or the LaunchAgent
#        from make runner-install)
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

max="$(setting RUNNER_MAX 2)"
min_idle="$(setting RUNNER_MIN_IDLE 1)"
spawn_seconds="$(setting RUNNER_SPAWN_SECONDS 300)"
health_port="$(setting ORCHESTRATOR_HEALTH_PORT 8080)"
readonly max min_idle spawn_seconds health_port

command -v claude >/dev/null 2>&1 || die "claude not found; brew install --cask claude-code@latest"
[[ -f "$RUNNER_CONFIG" ]] || die "no $RUNNER_CONFIG; cp vms/example-claude.env vms/runner.env"
((min_idle <= max)) || die "RUNNER_MIN_IDLE ($min_idle) is more than RUNNER_MAX ($max)"

# No hooks run until the orchestrator starts, so leftovers are safe to clear.
mkdir -p "$RUNNERS_DIR" "$AGENT_IMAGES_LOG_DIR"
reclaim_stale_runners 0

# An environment variable, not a file or an argument: nothing on disk, nothing
# in `ps`. hooks/spawn-runner unsets it.
SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET="$(keychain_secret claude-environment-secret orchestrator)" ||
	die "no Keychain item agent-images.claude-environment-secret; run make secret-set NAME=claude-environment-secret"
export SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET

log "orchestrator: up to $max runner VMs, $min_idle standby"
exec claude self-hosted-runner orchestrator \
	--hooks-dir "$AGENT_IMAGES_DIR/hooks" \
	--min-idle "$min_idle" \
	--hook-concurrency 1 \
	--expected-spawn-seconds "$spawn_seconds" \
	--health-port "$health_port"
