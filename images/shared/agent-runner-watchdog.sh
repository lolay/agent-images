#!/bin/bash
# Finds sessions whose git proxy port is refused and points them back at their
# live relay; when there's none, optionally stops the runner so the session
# requeues onto a fresh VM. Runs every 30 s from the com.agent-images.watchdog
# LaunchAgent (macOS), agent-runner-watchdog.timer (Linux VMs) or the cloud
# entrypoint's loop (Cloud Run).
#
# The failure (anthropics/claude-code#96856): a nested `claude` started inside a
# session (`claude plugin validate`, `claude mcp list`) rewrites the session's
# git proxy port to its own relay, then exits and closes it, so every git call
# fails until the session ends. The session's own relay is still up on its old
# port, so the repair is to find it and write its port back.
#
# A relay answers GET /__agentproxy/status with its port, the CA bundle it signs
# for, its README and its gh shim directory. The live loopback listener whose
# caBundlePath is the session gitconfig's sslCAInfo is that session's relay; its
# port goes into the gitconfig, and replaces the dead port in the README and the
# shim. A session with no live relay (Claude itself died) is counted instead:
# WATCHDOG_ACTION in runner.env decides what the second refusal in a row does,
# log (default) only records it, terminate also sends the runner SIGTERM, which
# drains and requeues the session (and with EPHEMERAL=true, powers this VM off).
#
# Relies on runner internals from that issue: <base-dir>/_sessions/<id>.gitconfig
# holding http.https://github.com/.proxy and .sslCAInfo, where <base-dir> is
# runner.env's CLAUDE_BASE_DIR (default ~/workspace, as in claude.sh), and the
# status endpoint's fields. If those change, it finds nothing and does nothing.
set -euo pipefail

readonly runner_env="$HOME/.config/agent-runner/runner.env"
# Nothing to watch until the VM is configured.
[[ -f "$runner_env" ]] || exit 0
# shellcheck source=/dev/null
base_dir="$(set -a && source "$runner_env" >/dev/null 2>&1 && printf '%s' "${CLAUDE_BASE_DIR:-}")" || base_dir=""
readonly sessions_dir="${base_dir:-$HOME/workspace}/_sessions"
os="$(uname -s)"
readonly os
if [[ "$os" == "Darwin" ]]; then
	readonly state_dir="$HOME/Library/Caches/agent-runner-watchdog"
else
	readonly state_dir="${XDG_CACHE_HOME:-$HOME/.cache}/agent-runner-watchdog"
fi
# Linux lists listening sockets here; the tests point it at a fixture.
readonly proc_net_tcp="${AGENT_WATCHDOG_PROC_NET_TCP:-/proc/net/tcp}"
readonly failures_to_act=2
# curl's exit code for "couldn't connect". Anything else means something answers.
readonly curl_connect_failed=7

log() { printf '%s watchdog: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
warn() { log "$@" >&2; }

action="$(sed -n -E 's/^WATCHDOG_ACTION=(.*)$/\1/p' "$runner_env" 2>/dev/null | tail -n 1)"
action="${action:-log}"

# The ports listening on 127.0.0.1, one per line.
loopback_ports() {
	if [[ "$os" == "Darwin" ]]; then
		lsof -nP -iTCP@127.0.0.1 -sTCP:LISTEN -Fn 2>/dev/null |
			sed -n -E 's/^n127\.0\.0\.1:([0-9]+)$/\1/p'
	else
		# local_address is hex, 127.0.0.1 little-endian; st 0A is LISTEN.
		local hex
		awk 'NR > 1 && $4 == "0A" { print $2 }' "$proc_net_tcp" 2>/dev/null |
			sed -n -E 's/^0100007F:([0-9A-Fa-f]{4})$/\1/p' |
			while read -r hex; do printf '%d\n' "$((16#$hex))"; done
	fi | sort -un
}

# A string field from the status JSON on stdin, pretty-printed or not.
json_string() {
	grep -o "\"$1\": *\"[^\"]*\"" | head -n 1 | sed -E 's/^"[^"]*": *"(.*)"$/\1/'
}

# The port of the live relay that signs with ca_bundle, other than dead_port.
live_relay_port() {
	local ca_bundle="$1" dead_port="$2" port status
	while read -r port; do
		[[ "$port" != "$dead_port" ]] || continue
		status="$(curl -s --max-time 2 "http://127.0.0.1:$port/__agentproxy/status" 2>/dev/null)" || continue
		grep -q '"enabled": *true' <<<"$status" || continue
		grep -Eq "\"port\": *$port([^0-9]|\$)" <<<"$status" || continue
		[[ "$(json_string caBundlePath <<<"$status")" == "$ca_bundle" ]] || continue
		printf '%s\n' "$port"
		printf '%s\n' "$status" >"$state_dir/relay.status"
		return 0
	done < <(loopback_ports)
	return 1
}

# Replace 127.0.0.1:<from> with 127.0.0.1:<to> in a file that has it, in place
# (same inode and mode: the shim stays executable).
swap_port() {
	local file="$1" from="$2" to="$3" swapped
	[[ -f "$file" ]] && grep -q "127\.0\.0\.1:$from\([^0-9]\|\$\)" "$file" || return 0
	swapped="$(mktemp "$state_dir/swap.XXXXXX")"
	sed -e "s/127\.0\.0\.1:$from\([^0-9]\)/127.0.0.1:$to\1/g" -e "s/127\.0\.0\.1:$from\$/127.0.0.1:$to/" \
		"$file" >"$swapped"
	cat "$swapped" >"$file"
	rm -f "$swapped"
}

# Point the session back at its live relay. Fails when there's none to find.
repair() {
	local session="$1" gitconfig="$2" proxy="$3" port="$4" ca_bundle live readme shim_dir shim
	ca_bundle="$(git config -f "$gitconfig" --get http.https://github.com/.sslcainfo 2>/dev/null)" || return 1
	live="$(live_relay_port "$ca_bundle" "$port")" || return 1
	git config -f "$gitconfig" --replace-all http.https://github.com/.proxy "${proxy/:$port/:$live}"
	readme="$(json_string readmePath <"$state_dir/relay.status")"
	shim_dir="$(json_string ghShimDir <"$state_dir/relay.status")"
	rm -f "$state_dir/relay.status"
	# Only the session's own files, whatever the status says.
	[[ "$readme" == "$sessions_dir/"* ]] && swap_port "$readme" "$port" "$live"
	if [[ "$shim_dir" == "$sessions_dir/"* && -d "$shim_dir" ]]; then
		for shim in "$shim_dir"/*; do swap_port "$shim" "$port" "$live"; done
	fi
	warn "session $session: git proxy port $port refused; repaired to the session's live relay on $live"
}

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

	if repair "$session" "$gitconfig" "$proxy" "$port"; then
		rm -f "$counter"
		continue
	fi

	failures=$(($(cat "$counter" 2>/dev/null || echo 0) + 1))
	printf '%s\n' "$failures" >"$counter"
	# Say it once at the threshold, not every 30 s after.
	if ((failures < failures_to_act)); then
		log "session $session: git proxy port $port refused, no live relay found ($failures/$failures_to_act)"
		continue
	fi
	((failures == failures_to_act)) || continue

	if [[ "$action" == "terminate" ]]; then
		warn "session $session: git proxy port $port is gone; stopping the runner so the session requeues"
		pkill -TERM -u "$(id -u)" -f '^claude self-hosted-runner( |$)' || warn "no runner process to stop"
	else
		warn "session $session: git proxy port $port is gone (WATCHDOG_ACTION=log, not acting)"
	fi
done
