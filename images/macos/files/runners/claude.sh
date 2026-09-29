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
#                           auto (default), true, or false: when the runner ends a
#                           session early (drain, idle release, failure), push its
#                           committed work so the session resumes from it. auto is
#                           on only without the git proxy: the release-time push uses
#                           git credentials on the VM, which the proxy setup doesn't
#                           have. With the proxy, the Stop hook asks Claude to push.
#   CLAUDE_CONFIGURE_GIT    true (default) = git identity Claude <noreply@anthropic.com>
#                           and Anthropic commit signing; the image has no identity.
#   CLAUDE_RELEASE_IDLE_SESSION_MIN
#                           60 (default) = release a session after that long with no
#                           user input, so it doesn't hold one of the host's VMs; it
#                           resumes on a fresh VM at the next message. Empty = never.
#   CLAUDE_KILL_SESSION_AFTER_MIN
#                           480 (default) = cap a session's life. Empty = never.
#   CLAUDE_CONFINE_REPO_SETTINGS
#                           enforce (default), warn, or off: refuse repos whose
#                           committed settings grant writes outside the workspace.
# Secret: ~/.claude-runner/environment-secret (mode 600).
#
# With the default --drain-grace-sec 0 and --capacity 1, the runner exits after
# one session; launchd restarts it, so each session gets a fresh registration.
set -euo pipefail

readonly secret_file="$HOME/.claude-runner/environment-secret"
readonly base_dir="$HOME/workspace"
: "${RUNNER_LABEL:=$(hostname -s)}"
: "${CLAUDE_USE_GIT_PROXY:=true}"
: "${CLAUDE_PUSH_OUTCOME_ON_RELEASE:=auto}"
: "${CLAUDE_CONFIGURE_GIT:=true}"
: "${CLAUDE_CONFINE_REPO_SETTINGS:=enforce}"
# No colon: set-but-empty means "never" rather than the default.
: "${CLAUDE_RELEASE_IDLE_SESSION_MIN=60}"
: "${CLAUDE_KILL_SESSION_AFTER_MIN=480}"

log() { printf '%s claude-runner: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
warn() { log "$@" >&2; }

if [[ ! -s "$secret_file" ]]; then
	warn "missing $secret_file; run make vm-configure"
	exit 1
fi

# Take the latest Claude Code on every start. A failed upgrade shouldn't block work.
# brew's output goes to this LaunchAgent's log like everything else.
# brew's chatter (including "already installed") goes to stdout; a failure is flagged
# on stderr.
brew upgrade --cask claude-code@latest 2>&1 || warn "claude upgrade failed; continuing"
log "claude $(claude --version)"

# Simulator devices are per user; this is the first point the guest user's GUI
# session exists. A missing simulator shouldn't keep the runner from serving
# sessions that don't need one.
agent-ensure-simulator || warn "no iPhone simulator; iOS simulator builds and UI tests will fail"

mkdir -p "$base_dir"

args=(
	self-hosted-runner
	--environment-secret-file "$secret_file"
	--client-label "$RUNNER_LABEL"
	--base-dir "$base_dir"
	--capacity 1
	--remove-session-state
	--confine-repo-settings "$CLAUDE_CONFINE_REPO_SETTINGS"
)
if [[ -n "${CLAUDE_LOCK_TO_ACCOUNT:-}" ]]; then
	args+=(--lock-to-account "$CLAUDE_LOCK_TO_ACCOUNT")
fi
if [[ "$CLAUDE_USE_GIT_PROXY" == "true" ]]; then
	args+=(--use-anthropic-git-proxy)
fi
push_outcome="$CLAUDE_PUSH_OUTCOME_ON_RELEASE"
if [[ "$push_outcome" == "auto" ]]; then
	push_outcome=true
	[[ "$CLAUDE_USE_GIT_PROXY" == "true" ]] && push_outcome=false
fi
if [[ "$push_outcome" == "true" ]]; then
	args+=(--push-outcome-on-release)
fi
if [[ "$CLAUDE_CONFIGURE_GIT" == "true" ]]; then
	args+=(--configure-git)
fi
if [[ -n "$CLAUDE_RELEASE_IDLE_SESSION_MIN" ]]; then
	args+=(--release-idle-session-min "$CLAUDE_RELEASE_IDLE_SESSION_MIN")
fi
if [[ -n "$CLAUDE_KILL_SESSION_AFTER_MIN" ]]; then
	args+=(--kill-session-after-min "$CLAUDE_KILL_SESSION_AFTER_MIN")
fi

exec claude "${args[@]}"
