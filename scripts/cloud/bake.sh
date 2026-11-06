#!/bin/bash
# Builds one cloud image with docker buildx bake (images/cloud/docker-bake.hcl),
# the way every local and pull request build does (make cloud-build, cloud-ci,
# cloud-build.yml):
#
#   bake.sh <type> [bake options]       a runner type, from its lock: the build
#                                       arguments scripts/cloud/lock.sh bake-args
#                                       gives, with the versions report
#                                       (versions-<type>, out/versions/<type>)
#   bake.sh orchestrator [bake options] the orchestrator, with Claude Code at
#                                       CLAUDE_VERSION (default latest): it has
#                                       no lock (specs/gcp.md, m5)
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
	CLAUDE_VERSION="${CLAUDE_VERSION:-latest}" exec "${bake[@]}" "$@" orchestrator
fi

mapfile -t args < <(scripts/cloud/lock.sh bake-args "$target")
# A failed process substitution doesn't fail mapfile; an empty list means it did.
((${#args[@]} > 0)) || {
	printf 'bake: no build arguments from %s'"'"'s lock\n' "$target" >&2
	exit 1
}
sets=()
for arg in "${args[@]}"; do
	sets+=(--set "$arg")
done
COMMIT="${COMMIT:-$(git rev-parse HEAD)}" exec "${bake[@]}" "${sets[@]}" "$@" "$target" "versions-$target"
