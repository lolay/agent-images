#!/bin/bash
# Builds one cloud image with docker buildx bake (images/cloud/docker-bake.hcl),
# the way every local and pull request build does (make cloud-build, cloud-ci,
# cloud-build.yml):
#
#   bake.sh <type> [bake options]       a runner type, from its lock: the build
#                                       arguments scripts/cloud/lock.sh bake-args
#                                       gives, with the versions report
#                                       (versions-<type>, out/versions/<type>)
#   bake.sh orchestrator [bake options] the orchestrator, with the newest Claude
#                                       Code (the bake file's
#                                       ORCHESTRATOR_CLAUDE_VERSION), or
#                                       CLAUDE_VERSION from the environment: it
#                                       has no lock (specs/gcp.md, m5)
#
# COMMIT names the commit in the versions report (default HEAD). The deploy
# builds with docker/bake-action instead, from the same lock.sh bake-args.
# Run from the repo root.
set -euo pipefail
shopt -s inherit_errexit

(($# >= 1)) || {
	printf 'usage: bake.sh <type>|orchestrator [bake options]\n' >&2
	exit 2
}
target="$1"
shift
readonly bake=(docker buildx bake -f images/cloud/docker-bake.hcl)

if [[ "$target" == orchestrator ]]; then
	# Unset or empty leaves the bake file's default, so it has no second copy
	# here (r3.f22); a runner-type pin in CLAUDE_VERSION's default never reaches it.
	if [[ -n "${CLAUDE_VERSION:-}" ]]; then
		export ORCHESTRATOR_CLAUDE_VERSION="$CLAUDE_VERSION"
	fi
	exec "${bake[@]}" "$@" orchestrator
fi

# Read into a variable, not `mapfile < <(...)`: a failed process substitution
# doesn't fail mapfile, so a bake-args that printed some lines and then died
# (an unknown line kind in the lock) built with a partial set of --set values.
# Here its exit status stops bake.sh (set -e, inherit_errexit) before any build.
output="$(scripts/cloud/lock.sh bake-args "$target")"
# A bake-args that exits 0 with nothing would build with no lock at all.
[[ -n "$output" ]] || {
	printf 'bake: no build arguments from %s'"'"'s lock\n' "$target" >&2
	exit 1
}
mapfile -t args <<<"$output"
sets=()
for arg in "${args[@]}"; do
	sets+=(--set "$arg")
done
COMMIT="${COMMIT:-$(git rev-parse HEAD)}" exec "${bake[@]}" "${sets[@]}" "$@" "$target" "versions-$target"
