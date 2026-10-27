#!/bin/bash
# Runs `claude self-hosted-runner` on this Mac itself, not in a VM, for work a
# VM can't do: Apple Intelligence isn't available in a macOS guest. One runner
# per host, restarted after every session (Claude's runner exits after one).
# Sessions run as the host user, with that user's git config, SSH keys, and
# Keychain, so the runner uses an environment (and secret) of its own and is
# always locked to one account.
#
# Usage: scripts/host-runner-run.sh [--check]
#   Started by scripts/host-runner-service.sh (make host-runner-run, or the
#   LaunchAgent from make host-runner-install) through the login shell in the
#   context directory, so it inherits that shell's environment for that
#   directory, and sessions inherit it in turn. --check prints what it would
#   run with and exits.
#
# From that environment:
#   CLAUDE_CONFIG_DIR  Seeds every session's Claude config; the account it's
#                      logged in to (oauthAccount.emailAddress) is the account
#                      the runner is locked to. Else HOST_CLAUDE_CONFIG_DIR (.env).
#   __active_context   fish's per-directory context: the secret is Keychain
#                      account host-<context>, else host.
# Settings: vms/host.env (vms/example-host.env); .env for HOST_RUNNER_HEALTH_PORT.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

check_only=false
[[ "${1:-}" == "--check" ]] && check_only=true
readonly check_only

[[ -f "$HOST_RUNNER_CONFIG" ]] || die "no $HOST_RUNNER_CONFIG; cp vms/example-host.env vms/host.env"
command -v claude >/dev/null 2>&1 || die "claude not found; brew install --cask claude-code@latest"

config_dir="${CLAUDE_CONFIG_DIR:-$(setting HOST_CLAUDE_CONFIG_DIR "")}"
[[ -n "$config_dir" ]] ||
	die "no Claude config directory: CLAUDE_CONFIG_DIR isn't set in $PWD's shell environment, and .env has no HOST_CLAUDE_CONFIG_DIR"
[[ -d "$config_dir" ]] || die "$config_dir (the Claude config directory) doesn't exist"
readonly config_dir
context="${__active_context:-}"
secret_accounts=(host)
[[ -z "$context" ]] || secret_accounts=("host-$context" host)
readonly context secret_accounts

set -a
# shellcheck source=/dev/null
source "$HOST_RUNNER_CONFIG"
set +a

# Both would rewrite the host user's own git config: the proxy deletes
# ~/.gitconfig and ~/.config/git before every session, and --configure-git
# writes ~/.gitconfig. Sessions use the host user's git and SSH keys instead.
: "${CLAUDE_USE_GIT_PROXY:=false}" "${CLAUDE_CONFIGURE_GIT:=false}"
[[ "$CLAUDE_USE_GIT_PROXY" == "false" ]] ||
	die "$HOST_RUNNER_CONFIG: CLAUDE_USE_GIT_PROXY must be false on the host; the git proxy deletes ~/.gitconfig and ~/.config/git"
[[ "$CLAUDE_CONFIGURE_GIT" == "false" ]] ||
	die "$HOST_RUNNER_CONFIG: CLAUDE_CONFIGURE_GIT must be false on the host; it overwrites ~/.gitconfig"

: "${CLAUDE_LOCK_TO_ACCOUNT:=$(claude_account_email "$config_dir")}"
[[ -n "$CLAUDE_LOCK_TO_ACCOUNT" ]] ||
	die "no account to lock to: $config_dir has no logged-in account (oauthAccount.emailAddress in .claude.json) and $HOST_RUNNER_CONFIG sets no CLAUDE_LOCK_TO_ACCOUNT; a session here runs as you"
host_name="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
: "${RUNNER_LABEL:=$(setting RUNNER_LABEL_PREFIX "$host_name")-host}"
: "${CLAUDE_BASE_DIR:=$HOME/agent-host-workspace}"
CLAUDE_HEALTH_PORT="$(setting HOST_RUNNER_HEALTH_PORT 8081)"
CLAUDE_UPGRADE=false
export CLAUDE_USE_GIT_PROXY CLAUDE_CONFIGURE_GIT CLAUDE_LOCK_TO_ACCOUNT RUNNER_LABEL \
	CLAUDE_BASE_DIR CLAUDE_HEALTH_PORT CLAUDE_UPGRADE
# The runner seeds each session's own CLAUDE_CONFIG_DIR from this.
export SELF_HOSTED_RUNNER_HOST_CONFIG_DIR="$config_dir"
unset CLAUDE_CONFIG_DIR

summary="context ${context:-none} ($PWD), Claude config $config_dir, locked to $CLAUDE_LOCK_TO_ACCOUNT, secret account ${secret_accounts[*]}, label $RUNNER_LABEL, /healthz on $CLAUDE_HEALTH_PORT, repos in $CLAUDE_BASE_DIR"
readonly summary

if $check_only; then
	log "host runner: $summary"
	status=0
	keychain_secret claude-environment-secret "${secret_accounts[@]}" >/dev/null || status=$?
	((status != 2)) || exit 1
	# Over SSH the login keychain is locked; the LaunchAgent reads it in the GUI session.
	((status == 0)) || printf 'warning: the LaunchAgent reads the secret in your GUI session, where the keychain is unlocked\n' >&2
	exit 0
fi

# One host runner per host: a second would take the same health port and
# compete for the same Simulator, ports, and checkouts.
readonly lock_dir="$AGENT_IMAGES_DIR/build/host-runner"
mkdir -p "$(dirname "$lock_dir")"
if ! mkdir "$lock_dir" 2>/dev/null; then
	other_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
	if [[ -n "$other_pid" ]] && kill -0 "$other_pid" 2>/dev/null; then
		die "a host runner is already running (pid $other_pid); see make host-runner-status"
	fi
	rm -rf "$lock_dir"
	mkdir "$lock_dir"
fi
printf '%s\n' "$$" >"$lock_dir/pid"
printf '%s\n' "$summary" >"$lock_dir/summary"

runner_pid=""
stopping=false
cleanup() {
	if [[ -n "$runner_pid" ]] && kill -0 "$runner_pid" 2>/dev/null; then
		kill -TERM "$runner_pid" 2>/dev/null || true
		wait "$runner_pid" 2>/dev/null || true
	fi
	rm -rf "$lock_dir"
}
trap cleanup EXIT
# launchd (uninstall) and Ctrl-C signal this script; the runner drains and
# pushes its session's work before exiting.
trap 'stopping=true; [[ -z "$runner_pid" ]] || kill -TERM "$runner_pid" 2>/dev/null || true' TERM INT

# An environment variable, not a file or an argument: nothing on disk, nothing
# in `ps`.
SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET="$(keychain_secret claude-environment-secret "${secret_accounts[@]}")" || exit 1
require_environment_key_shape "$SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET"
export SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET

log "host runner: $summary"
while ! $stopping; do
	clear_claude_quarantine
	"$AGENT_IMAGES_DIR/images/shared/runners/claude.sh" &
	runner_pid=$!
	# A trapped signal interrupts wait; wait again until the runner itself exits.
	while :; do
		if wait "$runner_pid"; then status=0; else status=$?; fi
		kill -0 "$runner_pid" 2>/dev/null || break
	done
	runner_pid=""
	$stopping && break
	log "host runner: runner exited ($status); starting the next one"
	# Back off so a bad secret doesn't turn into a tight restart loop.
	if ((status != 0)); then
		sleep 30 &
		wait $! || true
	fi
done
log "host runner: stopped"
