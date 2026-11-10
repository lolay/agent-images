#!/bin/bash
# merge-settings.sh <shared settings.json> <type settings.json>: prints the
# Claude Code settings a Cloud Run image seeds into sessions. Objects merge as
# jq's `*` does, the type's values winning, except that each hook event's list
# is the shared one's followed by the type's, so both images' SessionStart hooks
# run (images/shared/claude, images/cloud/runner/claude). Used by the Dockerfile.
set -euo pipefail

[[ $# -eq 2 ]] || {
	echo "usage: merge-settings.sh <shared settings.json> <type settings.json>" >&2
	exit 2
}
jq -s '
	(.[0].hooks // {}) as $shared | (.[1].hooks // {}) as $type
	| (.[0] * .[1])
	| if ($shared + $type) == {} then . else
		.hooks = reduce ($type | to_entries[]) as $event ($shared; .[$event.key] += $event.value)
	end' "$1" "$2"
