#!/bin/bash
# Writes a session's CLAUDE.md from an image's template (specs/image-toolsets.md):
# the template's `<!-- toolsets -->` line becomes the CLAUDE.md of every toolset
# install-toolset.sh installed, in install order, so the file lists what the image
# has and nothing else. Fails if the template has no such line.
#
# Usage: write-claude-md.sh <template> <output> [<record dir>]
#
# The record dir is where install-toolset.sh records toolsets
# (/etc/agent-images/toolsets): toolsets.txt, one name per line, and <name>.md
# for each toolset that has a CLAUDE.md.
set -euo pipefail

readonly marker='<!-- toolsets -->'

(($# == 2 || $# == 3)) || {
	printf 'usage: write-claude-md.sh <template> <output> [<record dir>]\n' >&2
	exit 2
}
template="$1" output="$2" record_dir="${3:-/etc/agent-images/toolsets}"
readonly template output record_dir

grep -qxF "$marker" "$template" || {
	printf 'write-claude-md: %s has no %s line\n' "$template" "$marker" >&2
	exit 1
}
[[ -f "$record_dir/toolsets.txt" ]] || {
	printf 'write-claude-md: no toolsets recorded in %s\n' "$record_dir" >&2
	exit 1
}

fragments="$(mktemp)"
trap 'rm -f "$fragments"' EXIT
while read -r name; do
	[[ ! -f "$record_dir/$name.md" ]] || cat "$record_dir/$name.md" >>"$fragments"
done <"$record_dir/toolsets.txt"

# Written next to the output and moved into place, so a failure never leaves
# a half-written CLAUDE.md.
awk -v marker="$marker" -v fragments="$fragments" '
	$0 == marker { while ((getline line < fragments) > 0) print line; next }
	{ print }
' "$template" >"$output.tmp"
mv "$output.tmp" "$output"
