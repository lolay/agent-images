#!/bin/bash
# Checks a built runner image's toolset record against the source (r2.f1): the
# image recorded exactly the toolsets its Dockerfiles install, in order, and a
# binary check file and a smoke test for each toolset whose source has one. A
# record that lost them would let the smoke pass having checked nothing, since
# agent-toolset-checks can only run what was recorded. make cloud-smoke runs it
# before the smoke, so the pull request check and a local smoke both do.
#
# Usage: record-check.sh <type> <image>   (from the repo root)
set -euo pipefail

# shellcheck source=scripts/cloud/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

(($# == 2)) || {
	printf 'usage: record-check.sh <type> <image>\n' >&2
	exit 2
}
type="$1" image="$2"

mapfile -t expected < <(cloud_toolsets "$type")
((${#expected[@]} > 0)) || {
	printf 'record: no toolsets for %s in images/cloud\n' "$type" >&2
	exit 2
}

# The image's profile (BASH_ENV) sets AGENT_TOOLSETS_DIR, so the path isn't
# spelled out here. One listing: toolsets.txt, a separator, then the files.
listing="$(docker run --rm --entrypoint /bin/bash "$image" -c \
	'cd "${AGENT_TOOLSETS_DIR:?}" && cat toolsets.txt && echo --- && ls -1')"
recorded=() files=" " seen_separator=false
while IFS= read -r line; do
	if [[ "$line" == --- ]]; then
		seen_separator=true
	elif $seen_separator; then
		files+="$line "
	else
		recorded+=("$line")
	fi
done <<<"$listing"
$seen_separator || {
	printf 'record: FAIL %s (no record listing from %s)\n' "$type" "$image"
	exit 1
}

problems=()
[[ "${recorded[*]}" == "${expected[*]}" ]] ||
	problems+=("recorded toolsets [${recorded[*]}], Dockerfiles install [${expected[*]}]")
checks=0 smokes=0
for name in "${expected[@]}"; do
	if [[ -f "images/toolsets/$name/check.txt" ]]; then
		checks=$((checks + 1))
		[[ "$files" == *" $name.check "* ]] || problems+=("no $name.check")
	fi
	if [[ -f "images/toolsets/$name/smoke.sh" ]]; then
		smokes=$((smokes + 1))
		[[ "$files" == *" $name.smoke.sh "* ]] || problems+=("no $name.smoke.sh")
	fi
done

if ((${#problems[@]} > 0)); then
	printf 'record: FAIL %s (%s)\n' "$type" "$(
		IFS=';'
		printf '%s' "${problems[*]}"
	)"
	exit 1
fi
printf 'record: pass %s (%d toolsets, %d with binary checks, %d with smoke tests)\n' \
	"$type" "${#expected[@]}" "$checks" "$smokes"
