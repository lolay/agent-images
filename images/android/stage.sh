#!/bin/bash
# Assembles the Android image's staged layout (images/android/staging.txt) in a
# new local directory, for a build to copy into its build VM whole. Every build
# stages through this, so the layout is written once.
#
# Usage: images/android/stage.sh <dest> [variant]
#   <dest> must not exist yet, or be empty. [variant] adds the list's lines for
#   that build (gce: the Compute Engine image).
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly repo
readonly list="$repo/images/android/staging.txt"
readonly dest="${1:?usage: stage.sh <dest> [variant]}"
readonly variant="${2:-}"

die() {
	printf 'stage.sh: %s\n' "$*" >&2
	exit 1
}

if [[ -e "$dest" ]] && [[ -n "$(ls -A "$dest")" ]]; then
	die "$dest isn't empty"
fi
mkdir -p "$dest"

variant_lines=0
while read -r src target line_variant extra; do
	[[ -n "$src" && "$src" != \#* ]] || continue
	[[ -n "$target" && -z "$extra" ]] || die "$list: bad line: $src $target $line_variant $extra"
	[[ -z "$line_variant" || "$line_variant" == "$variant" ]] || continue
	[[ -z "$line_variant" ]] || variant_lines=$((variant_lines + 1))
	[[ -e "$repo/$src" ]] || die "$list: no $src"
	mkdir -p "$dest/$target"
	if [[ -d "$repo/$src" ]]; then
		cp -R "$repo/$src/." "$dest/$target/"
	else
		cp "$repo/$src" "$dest/$target/"
	fi
done <"$list"
# A misspelled variant would stage the common layout alone and pass unnoticed.
[[ -z "$variant" || "$variant_lines" -gt 0 ]] || die "$list has no lines for variant $variant"
