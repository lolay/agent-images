#!/bin/bash
# Runs a Cursor Cloud Agent worker for this VM. Called by agent-runner with
# runner.env already exported. One worker per VM: a worker holds one session
# at a time, which keeps the VM at one build and one simulator.
#
# Settings (runner.env):
#   RUNNER_LABEL                 Worker name (the VM name).
#   CURSOR_REPOSITORY_URL        HTTPS URL of the repo the worker serves, e.g.
#                                the nowline-workspace repo. Cursor routes
#                                sessions to workers by this repo.
#   CURSOR_REF                   Optional branch or tag to check out.
#   CURSOR_POOL_NAME             Set for team pools (service account key).
#                                Empty = My Machines (individual plans).
#   CURSOR_IDLE_RELEASE_TIMEOUT  Pool mode only, seconds (default 600).
# Secrets:
#   ~/.config/agent-runner/secrets/cursor-api-key  (mode 600)
#   ~/.config/agent-runner/secrets/git-token       (mode 600, optional; needed
#                                                   for private repos and for
#                                                   cloning sibling repos)
set -euo pipefail

readonly secrets_dir="$AGENT_RUNNER_CONFIG_DIR/secrets"
readonly api_key_file="$secrets_dir/cursor-api-key"
readonly git_token_file="$secrets_dir/git-token"
readonly management_addr="127.0.0.1:8081"
: "${RUNNER_LABEL:=$(hostname -s)}"
: "${CURSOR_REPOSITORY_URL:?CURSOR_REPOSITORY_URL is not set in runner.env}"
: "${CURSOR_IDLE_RELEASE_TIMEOUT:=600}"

log() { printf '%s cursor-worker: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

if [[ ! -s "$api_key_file" ]]; then
	log "missing $api_key_file; run make vm-configure"
	exit 1
fi
CURSOR_API_KEY="$(<"$api_key_file")"
export CURSOR_API_KEY

configure_git_auth() {
	if [[ ! -s "$git_token_file" ]]; then
		return
	fi
	local host
	host="$(printf '%s' "$CURSOR_REPOSITORY_URL" | sed -E 's#^https://([^/]+)/.*#\1#')"
	(
		umask 077
		printf 'https://git:%s@%s\n' "$(<"$git_token_file")" "$host" >"$HOME/.git-credentials"
	)
	git config --global credential.helper store
}

sync_repo() {
	local dir="$1"
	if [[ ! -d "$dir/.git" ]]; then
		rm -rf "$dir"
		git clone "$CURSOR_REPOSITORY_URL" "$dir"
	else
		git -C "$dir" remote set-url origin "$CURSOR_REPOSITORY_URL"
		git -C "$dir" fetch origin --tags --prune
	fi
	if [[ -n "${CURSOR_REF:-}" ]]; then
		git -C "$dir" fetch origin "$CURSOR_REF"
		git -C "$dir" checkout --detach FETCH_HEAD
	fi
}

repo_name="$(basename "$CURSOR_REPOSITORY_URL" .git)"
readonly worker_dir="$HOME/workspace/$repo_name"
mkdir -p "$HOME/workspace"

# Take the latest Cursor CLI on every start. A failed upgrade shouldn't block work.
brew upgrade --cask cursor-cli >/dev/null 2>&1 ||
	log "cursor upgrade failed; continuing with $(cursor-agent --version)"

configure_git_auth
sync_repo "$worker_dir"

if [[ -n "${CURSOR_POOL_NAME:-}" ]]; then
	args=(
		worker
		--pool
		--pool-name "$CURSOR_POOL_NAME"
		--worker-dir "$worker_dir"
		--idle-release-timeout "$CURSOR_IDLE_RELEASE_TIMEOUT"
		--management-addr "$management_addr"
		start
	)
else
	args=(
		worker start
		--worker-dir "$worker_dir"
		--name "$RUNNER_LABEL"
		--management-addr "$management_addr"
	)
fi

mode="${CURSOR_POOL_NAME:+pool $CURSOR_POOL_NAME}"
log "serving $repo_name (${mode:-My Machines})"
exec cursor-agent "${args[@]}"
