#!/bin/bash
# agent-toolset-checks: runs the binary checks and smoke tests the image's
# toolsets recorded (install-toolset.sh; specs/gcp.md, m4). The one runner for the
# cloud images' smoke (images/cloud/entrypoint.sh), the android smoke VM
# (agent-gce-smoke) and sessions (the /agent-toolset-checks skill), so the
# timeout, the temp directory and the offline settings live here only.
#
#   agent-toolset-checks versions
#       Every binary check (<name>.check, `label: command`), in install order:
#       `label: <first line of output>`, or `label: missing (<last line>)` for one
#       that failed. Exits 1 if any failed, and 1 if no recorded toolset has a
#       check file at all: an empty record checks nothing and must not pass.
#   agent-toolset-checks smoke-list
#       The toolsets with a smoke test (<name>.smoke.sh), in install order.
#   agent-toolset-checks live <name>
#       Exits 0 and prints why if <name>'s smoke test runs only live (a
#       `# smoke: live (<why>)` line in its header), 1 if a hosted runner can run it.
#   agent-toolset-checks smoke <name> [--brief]
#       Runs <name>'s smoke test in a fresh temp directory under $TMPDIR, removed
#       afterward, under a timeout, with the toolchains' network off. Prints its
#       output (--brief: its last line on a pass, its last lines on a failure)
#       and exits with its status (124 when it timed out).
#
#   AGENT_TOOLSETS_DIR        the record (default /etc/agent-images/toolsets)
#   AGENT_SMOKE_TIMEOUT_SEC   each smoke test's limit (default 120)
#   AGENT_SMOKE_KILL_AFTER_SEC  how long after the limit's TERM a test that
#                             ignores it is killed (default 5)
set -euo pipefail

readonly dir="${AGENT_TOOLSETS_DIR:-/etc/agent-images/toolsets}"
readonly timeout_sec="${AGENT_SMOKE_TIMEOUT_SEC:-120}"
readonly kill_after_sec="${AGENT_SMOKE_KILL_AFTER_SEC:-5}"

die() {
	printf 'agent-toolset-checks: %s\n' "$*" >&2
	exit 2
}

usage() { die "usage: agent-toolset-checks versions | smoke-list | live <name> | smoke <name> [--brief]"; }

# The toolchains fetch nothing: a smoke test proves the image works from what it
# holds. Data a test needs comes from a URL the test names, not from these.
readonly offline=(GOPROXY=off GOFLAGS=-mod=mod GOTOOLCHAIN=local CARGO_NET_OFFLINE=true
	UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never npm_config_offline=true CHECKPOINT_DISABLE=1)

versions() {
	local name line label command output failed=0 checked=0
	while read -r name; do
		[[ -f "$dir/$name.check" ]] || continue
		checked=1
		while IFS= read -r line; do
			[[ -n "$line" && "$line" != \#* ]] || continue
			label="${line%%: *}" command="${line#*: }"
			if output="$(CHECKPOINT_DISABLE=1 bash -c "$command" 2>&1 </dev/null)"; then
				printf '%s: %s\n' "$label" "$(head -n 1 <<<"$output")"
			else
				# The check's last line says why (a missing proxy, a failed command).
				printf '%s: missing (%s)\n' "$label" "${output##*$'\n'}"
				failed=1
			fi
		done <"$dir/$name.check"
	done <"$dir/toolsets.txt"
	if ((! checked)); then
		printf 'agent-toolset-checks: no recorded toolset has a .check file in %s: nothing was checked\n' "$dir" >&2
		return 1
	fi
	return "$failed"
}

smoke_list() {
	local name
	while read -r name; do
		[[ ! -f "$dir/$name.smoke.sh" ]] || printf '%s\n' "$name"
	done <"$dir/toolsets.txt"
}

script_of() {
	[[ -f "$dir/$1.smoke.sh" ]] || die "no smoke test for $1 in $dir"
	printf '%s' "$dir/$1.smoke.sh"
}

live() {
	local script why
	script="$(script_of "$1")"
	why="$(sed -n -E '1,10s/^# smoke: live[[:space:]]*\((.*)\)[[:space:]]*$/\1/p' "$script" | head -n 1)"
	[[ -n "$why" ]] || return 1
	printf '%s\n' "$why"
}

smoke() {
	local name="$1" brief="${2:-}" script work log pid output status=0 started ended
	script="$(script_of "$name")"
	mkdir -p "${TMPDIR:-/tmp}"
	work="$(mktemp -d "${TMPDIR:-/tmp}/smoke-$name.XXXXXX")"
	log="$work.log"
	# shellcheck disable=SC2064 # expand now: they're local
	trap "rm -rf '$work' '$log'" EXIT
	# Its own process group, killed whole afterward: a child the test left
	# running (one that ignores TERM, a daemon) would otherwise outlive the
	# timeout. Output goes to a file, which such a child can't hold open.
	started="${EPOCHREALTIME/./}"
	(cd "$work" && exec setsid env "${offline[@]}" timeout --kill-after="$kill_after_sec" "$timeout_sec" bash "$script") \
		>"$log" 2>&1 </dev/null &
	pid=$!
	wait "$pid" || status=$?
	ended="${EPOCHREALTIME/./}"
	kill -KILL -- "-$pid" 2>/dev/null || true
	# When --kill-after fires, timeout KILLs its own process group, itself
	# included, so the status is 137, not 124. A 137 at or after the limit is
	# that; an earlier one is the test's own.
	if ((status == 137 && ended - started >= timeout_sec * 1000000)); then
		status=124
	fi
	output="$(cat "$log")"
	((status != 124)) || output="${output:+$output$'\n'}$name: timed out after ${timeout_sec}s"
	if [[ "$brief" != --brief ]]; then
		printf '%s\n' "$output"
	elif ((status == 0)); then
		printf '%s\n' "${output##*$'\n'}"
	else
		tail -n 5 <<<"$output"
	fi
	return "$status"
}

# Checked here, not in a process substitution, so a missing record fails the call.
[[ -s "$dir/toolsets.txt" ]] || die "no toolsets recorded in $dir"

case "${1:-}" in
versions) (($# == 1)) || usage && versions ;;
smoke-list) (($# == 1)) || usage && smoke_list ;;
live) (($# == 2)) || usage && live "$2" ;;
smoke)
	{ (($# == 2)) || { (($# == 3)) && [[ "$3" == --brief ]]; }; } || usage
	smoke "$2" "${3:-}"
	;;
*) usage ;;
esac
