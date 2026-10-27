#!/bin/bash
# Runs the host runner (host-runner-run.sh) in the foreground, or installs,
# removes, or reports its LaunchAgent.
#
# Usage: scripts/host-runner-service.sh run|install|uninstall|status [<dir>]
#
# <dir> (default: the current directory) picks the context. The runner starts
# through the host user's login shell in <dir>, so it gets the environment a
# shell opened there would have: with fish's per-context config, that
# directory's markers (CLAUDE_CONFIG_DIR and the rest) and loadenv files.
# run and the LaunchAgent both start it from launchd's minimal environment, so
# they see the same thing.
#
# install starts the runner at once, in the GUI session (Simulator and Apple
# Intelligence need one; a process started from SSH isn't in it), and again at
# every login until uninstall. To run it only when needed, install it over SSH
# and uninstall it after.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

readonly usage="usage: host-runner-service.sh run|install|uninstall|status [<dir>]"
readonly action="${1:?$usage}"
context_dir="${2:-$PWD}"
readonly label="com.agent-images.host-runner"
readonly plist="$HOME/Library/LaunchAgents/$label.plist"
readonly log_base="$AGENT_IMAGES_LOG_DIR/host-runner"
readonly lock_dir="$AGENT_IMAGES_DIR/build/host-runner"
readonly run_script="$AGENT_IMAGES_DIR/scripts/host-runner-run.sh"
domain="$(launchd_domain)"
readonly domain

is_loaded() { launchctl print "$domain/$label" >/dev/null 2>&1; }

login_shell() {
	local user_shell
	user_shell="$(setting HOST_RUNNER_SHELL "")"
	if [[ -z "$user_shell" ]]; then
		user_shell="$(dscl . -read "/Users/$(id -un)" UserShell 2>/dev/null | awk '{ print $2 }')"
	fi
	printf '%s' "${user_shell:-/bin/zsh}"
}

# What launchd gives a LaunchAgent, so a run from an SSH shell matches one.
clean_env=(
	env -i
	HOME="$HOME"
	USER="$(id -un)"
	LOGNAME="$(id -un)"
	TMPDIR="$(getconf DARWIN_USER_TEMP_DIR 2>/dev/null || printf '/tmp/')"
	PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
)
# Sessions push with the host user's SSH keys; an agent forwarded over SSH counts.
[[ -z "${SSH_AUTH_SOCK:-}" ]] || clean_env+=(SSH_AUTH_SOCK="$SSH_AUTH_SOCK")

# Sets runner_command: cd to the context directory, start the login shell
# there (which reads its config for that directory), and run host-runner-run.sh
# from it. The script's path goes inside the shell's -c string in single
# quotes, which fish, zsh, and bash read alike for a path without one; no
# && in it either, so the command stays valid plist text.
set_runner_command() {
	local shell_path
	shell_path="$(login_shell)"
	# shellcheck disable=SC2016 # expanded by that bash, not here
	runner_command=(
		/bin/bash -c 'cd "$1" || exit 1; SHELL="$2" exec "$2" -l -c "$3"' host-runner
		"$context_dir" "$shell_path" "exec /bin/bash '$run_script' $*"
	)
}

preflight() {
	command -v claude >/dev/null 2>&1 || die "claude not found; brew install --cask claude-code@latest"
	[[ -f "$HOST_RUNNER_CONFIG" ]] || die "no $HOST_RUNNER_CONFIG; cp vms/example-host.env vms/host.env"
	[[ -d "$context_dir" ]] || die "$context_dir isn't a directory"
	context_dir="$(cd "$context_dir" && pwd)"
	[[ "$run_script" != *"'"* ]] || die "$AGENT_IMAGES_DIR has a single quote in its path; move the checkout"
}

runner_pid() { cat "$lock_dir/pid" 2>/dev/null || true; }

case "$action" in
run)
	preflight
	is_loaded && die "$label is installed and runs the host runner already; make host-runner-uninstall first"
	set_runner_command
	log "starting the host runner from $context_dir"
	exec "${clean_env[@]}" "${runner_command[@]}"
	;;
install)
	preflight
	is_loaded && die "$label is already installed; run make host-runner-uninstall first"
	pid="$(runner_pid)"
	if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
		die "a host runner is already running (pid $pid), likely make host-runner-run; stop it first"
	fi
	set_runner_command --check
	"${clean_env[@]}" "${runner_command[@]}"
	set_runner_command
	mkdir -p "$(dirname "$plist")" "$AGENT_IMAGES_LOG_DIR"
	write_host_plist "$plist" "$label" "$log_base" true "${runner_command[@]}"
	launchctl bootstrap "$domain" "$plist"
	log "installed $label from $context_dir (logs: $log_base.out, $log_base.err)"
	;;
uninstall)
	pid="$(runner_pid)"
	if is_loaded; then
		launchctl bootout "$domain/$label"
		# bootout returns before the runner has drained and pushed its session's work.
		waited=0
		while [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && ((waited < 130)); do
			sleep 2
			waited=$((waited + 2))
		done
	fi
	rm -f "$plist"
	log "uninstalled $label"
	;;
status)
	if is_loaded; then
		state="$(launchctl print "$domain/$label" | sed -n -E 's/^[[:space:]]*state = (.*)$/\1/p' | head -n 1)"
		printf 'host runner  %s (%s)\n' "$label" "${state:-loaded}"
	else
		printf 'host runner  not installed\n'
	fi
	pid="$(runner_pid)"
	if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
		printf '  running     pid %s: %s\n' "$pid" "$(cat "$lock_dir/summary" 2>/dev/null || true)"
		health="$(curl -fsS --max-time 3 "http://127.0.0.1:$(setting HOST_RUNNER_HEALTH_PORT 8081)/healthz" 2>/dev/null || true)"
		printf '  healthz     %s\n' "${health:-no response}"
	else
		printf '  not running\n'
	fi
	for stream in out err; do
		if [[ -s "$log_base.$stream" ]]; then
			printf '  %s\n' "$(basename "$log_base.$stream")"
			tail -n "${LOG_LINES:-5}" "$log_base.$stream" | sed 's/^/    /'
		fi
	done
	;;
*)
	die "unknown action $action (run, install, uninstall, or status)"
	;;
esac
