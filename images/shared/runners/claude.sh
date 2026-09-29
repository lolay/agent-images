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
# one session; launchd (systemd on Linux) restarts it, so each session gets a
# fresh registration.
set -euo pipefail

readonly secret_file="$HOME/.claude-runner/environment-secret"
readonly base_dir="$HOME/workspace"
: "${RUNNER_LABEL:=$(hostname -s)}"
: "${CLAUDE_USE_GIT_PROXY:=true}"
: "${CLAUDE_PUSH_OUTCOME_ON_RELEASE:=true}"
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
# The upgrade's chatter (including "already installed") goes to stdout, into the
# runner's log like everything else; a failure is flagged on stderr. macOS
# installs Claude as a Homebrew cask, Linux with the native installer.
if command -v brew >/dev/null 2>&1; then
	brew upgrade --cask claude-code@latest 2>&1 || warn "claude upgrade failed; continuing"
else
	claude update 2>&1 || warn "claude update failed; continuing"
fi
log "claude $(claude --version)"

# Devices the image provides. A missing device shouldn't keep the runner from
# serving sessions that don't need one.
# macOS: simulator devices are per user, and this is the first point the guest
# user's GUI session exists.
if command -v agent-ensure-simulator >/dev/null 2>&1; then
	agent-ensure-simulator || warn "no iPhone simulator; iOS simulator builds and UI tests will fail"
fi
# Linux: boot the Android emulator in the background, so it's up by the time a
# session needs it (sessions run `agent-emulator wait` first).
if command -v agent-emulator >/dev/null 2>&1; then
	agent-emulator start || warn "Android emulator didn't start; instrumented tests will fail"
fi

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
if [[ "$CLAUDE_PUSH_OUTCOME_ON_RELEASE" == "true" ]]; then
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
