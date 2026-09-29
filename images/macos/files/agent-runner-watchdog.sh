#!/bin/bash
# Finds sessions whose git proxy relay is gone and, optionally, stops the runner
# so the session requeues onto a fresh VM. Runs every 30 s from the
# com.agent-images.watchdog LaunchAgent.
#
# The failure (anthropics/claude-code#96856): a nested `claude` started inside a
# session rewrites the session's git proxy port to its own relay, then exits and
# closes it, so every git and gh call fails until the session ends.
#
# WATCHDOG_ACTION in runner.env: log (default) only records it; terminate also
# sends the runner SIGTERM, which drains and requeues the session (and with
# EPHEMERAL=true, powers this VM off).
#
# Relies on runner internals from that issue: <base-dir>/_sessions/<id>.gitconfig
# holding http.https://github.com/.proxy. If those change, it finds nothing and
# does nothing.
set -euo pipefail

readonly runner_env="$HOME/.config/agent-runner/runner.env"
readonly sessions_dir="$HOME/workspace/_sessions"
readonly state_dir="$HOME/Library/Caches/agent-runner-watchdog"
readonly failures_to_act=2
# curl's exit code for "couldn't connect". Anything else means something answers.
readonly curl_connect_failed=7

log() { printf '%s watchdog: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

action="$(sed -n -E 's/^WATCHDOG_ACTION=(.*)$/\1/p' "$runner_env" 2>/dev/null | tail -n 1)"
action="${action:-log}"

mkdir -p "$state_dir"
shopt -s nullglob

# Forget sessions that have ended.
for counter in "$state_dir"/*.failures; do
	[[ -f "$sessions_dir/$(basename "$counter" .failures).gitconfig" ]] || rm -f "$counter"
done

for gitconfig in "$sessions_dir"/*.gitconfig; do
	session="$(basename "$gitconfig" .gitconfig)"
	proxy="$(git config -f "$gitconfig" --get http.https://github.com/.proxy 2>/dev/null)" || continue
	port="$(printf '%s' "$proxy" | sed -n -E 's#^(https?://)?(127\.0\.0\.1|localhost):([0-9]+).*$#\3#p')"
	[[ -n "$port" ]] || continue
	counter="$state_dir/$session.failures"

	status=0
	curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$port/__agentproxy/status" || status=$?
	if ((status != curl_connect_failed)); then
		rm -f "$counter"
		continue
	fi

	failures=$(($(cat "$counter" 2>/dev/null || echo 0) + 1))
	printf '%s\n' "$failures" >"$counter"
	# Say it once at the threshold, not every 30 s after.
	if ((failures < failures_to_act)); then
		log "session $session: git proxy port $port refused ($failures/$failures_to_act)"
		continue
	fi
	((failures == failures_to_act)) || continue

	if [[ "$action" == "terminate" ]]; then
		log "session $session: git proxy port $port is gone; stopping the runner so the session requeues"
		pkill -TERM -u "$(id -u)" -f '^claude self-hosted-runner( |$)' || log "no runner process to stop"
	else
		log "session $session: git proxy port $port is gone (WATCHDOG_ACTION=log, not acting)"
	fi
done
