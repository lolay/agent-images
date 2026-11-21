#!/bin/bash
# The orchestrator hooks' exit contract, shared by hooks/spawn-runner (macOS),
# hooks/linux/spawn-runner and hooks/gcp/spawn-runner. Sourced, not executed;
# no platform code, so the orchestrator image copies it beside hooks/gcp/.
#
# Exit codes: 0 submitted or already submitted; 1 retry later; 2 don't retry
# (the setup is broken). A hook exits on purpose through fail, which sets
# failed_on_purpose; its own EXIT trap maps any other non-zero exit (set -e) to
# 1, since 2 or more would read as "never retry".

# shellcheck disable=SC2034 # read by each hook's EXIT trap
failed_on_purpose=false

# fail <status> <message>: the message to stderr, whose tail shows in the
# environment's Activity tab, so it never carries a secret.
fail() {
	printf 'agent-images: %s\n' "$2" >&2
	failed_on_purpose=true
	exit "$1"
}

# require <where> NAME...: exits 2 for the first NAME unset or empty, naming it
# and where it's set. Not `${X:?}`, whose exit (1, or 127 at top level) isn't
# fail's, so the trap turned it into a retry that would find it missing too,
# forever (r1.f22).
require() {
	local where="$1" name
	shift
	for name; do
		[[ -n "${!name:-}" ]] || fail 2 "$name is not set; $where"
	done
}
