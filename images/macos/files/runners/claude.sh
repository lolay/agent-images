#!/bin/bash
# Runs `claude self-hosted-runner` for this VM. Called by agent-runner with
# runner.env already exported.
#
# Settings (runner.env):
#   RUNNER_LABEL            Label shown in the Anthropic console (the VM name).
#   CLAUDE_LOCK_TO_ACCOUNT  Optional. Only this account's sessions are assigned.
#   CLAUDE_USE_GIT_PROXY    true (default) = Anthropic-managed git auth; no git
#                           credentials on the VM. Replaces ~/.gitconfig.
#   CLAUDE_PUSH_OUTCOME_ON_RELEASE
#                           true (default) = when the runner ends a session early
#                           (drain, idle release, failure), push its committed work
#                           so the session resumes from it on the next runner.
# Secret: ~/.claude-runner/environment-secret (mode 600).
#
# With the default --drain-grace-sec 0 and --capacity 1, the runner exits after
# one session; launchd restarts it, so each session gets a fresh registration.
set -euo pipefail

readonly secret_file="$HOME/.claude-runner/environment-secret"
readonly base_dir="$HOME/workspace"
: "${RUNNER_LABEL:=$(hostname -s)}"
: "${CLAUDE_USE_GIT_PROXY:=true}"
: "${CLAUDE_PUSH_OUTCOME_ON_RELEASE:=true}"

log() { printf '%s claude-runner: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

if [[ ! -s "$secret_file" ]]; then
	log "missing $secret_file; run make vm-configure"
	exit 1
fi

# Take the latest Claude Code on every start. A failed upgrade shouldn't block work.
# brew's output goes to this LaunchAgent's log like everything else.
brew upgrade --cask claude-code@latest 2>&1 || log "claude upgrade failed; continuing"
log "claude $(claude --version)"

mkdir -p "$base_dir"

args=(
	self-hosted-runner
	--environment-secret-file "$secret_file"
	--client-label "$RUNNER_LABEL"
	--base-dir "$base_dir"
	--capacity 1
	--remove-session-state
)
if [[ -n "${CLAUDE_LOCK_TO_ACCOUNT:-}" ]]; then
	args+=(--lock-to-account "$CLAUDE_LOCK_TO_ACCOUNT")
fi
if [[ "$CLAUDE_USE_GIT_PROXY" == "true" ]]; then
	args+=(--use-anthropic-git-proxy)
fi
if [[ "$CLAUDE_PUSH_OUTCOME_ON_RELEASE" == "true" ]]; then
	args+=(--push-outcome-on-release)
fi

exec claude "${args[@]}"
