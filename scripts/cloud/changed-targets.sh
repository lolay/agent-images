#!/bin/bash
# What a push to main redeploys (specs/gcp.md, "What a push to main deploys"):
#
#   changed-targets.sh <base> <head>    the targets the diff touches
#   changed-targets.sh --all            every target
#   changed-targets.sh --schedule <class>
#                                       the types whose REBUILD is <class>
#                                       (nightly or weekly; unset is nightly),
#                                       and the orchestrator with nightly
#
# Prints {"types": [{"type": "linux", "build": true}, ...], "vms": [{"type":
# "android"}, ...], "orchestrator": bool, "prune": bool}. types are the Cloud
# Run types (BACKEND=run, the default): build is false when only the type's env
# file changed, so the deploy rewrites the job and reuses the live image. vms
# are the Compute Engine types (BACKEND=compute) whose image needs a Packer
# build; their env file alone rebuilds nothing, since the hook reads it from
# the orchestrator image. prune is true when a type's env file was deleted, so
# its jobs and images go too. A base that isn't a commit here (none yet, or
# gone after a force push) means everything. Run from the repo root.
set -euo pipefail

all=false
schedule=""
if [[ "${1:-}" == "--all" ]]; then
	all=true
elif [[ "${1:-}" == "--schedule" ]]; then
	if (($# != 2)) || [[ ! "$2" =~ ^(nightly|weekly)$ ]]; then
		printf 'usage: %s --schedule nightly|weekly\n' "$(basename "$0")" >&2
		exit 2
	fi
	schedule="$2"
elif (($# == 2)); then
	base="$1" head="$2"
	if [[ -z "$base" ]] || ! git cat-file -e "$base^{commit}" 2>/dev/null; then
		all=true
	fi
else
	printf 'usage: %s <base> <head> | --all | --schedule nightly|weekly\n' "$(basename "$0")" >&2
	exit 2
fi

# shellcheck source=scripts/cloud/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
AGENT_GCP_ENV_DIR=infra/gcp
mapfile -t types < <(gcp_types)

changed=()
deleted=()
if ! $all && [[ -z "$schedule" ]]; then
	mapfile -t changed < <(git diff --name-only "$base" "$head")
	mapfile -t deleted < <(git diff --name-only --diff-filter=D "$base" "$head" -- 'infra/gcp/*.env')
fi

touched() {
	local pattern path
	for path in "${changed[@]}"; do
		for pattern in "$@"; do
			# shellcheck disable=SC2053 # the patterns are globs on purpose
			[[ "$path" == $pattern ]] && return 0
		done
	done
	return 1
}

# What every type is built from. A toolset's own directory rebuilds only the
# types that install it (cloud_image_paths); the scripts beside them, every type.
# (A * in these patterns also matches /, so the scripts are named outright.)
readonly every_type=(
	"images/shared/*" "images/toolsets/install-toolset.sh" "images/toolsets/write-claude-md.sh"
	"images/toolsets/toolset-checks.sh" "images/toolsets/write-versions.sh" "images/toolsets/skills/*"
	"images/cloud/entrypoint.sh" "images/cloud/runner/*"
	"images/cloud/docker-bake.hcl" ".dockerignore" "infra/gcp/project.env"
)

# vm_image_paths <type>: a Compute Engine type's image is built by Packer from
# images/<type> (images/<type>/gce), with the toolsets in its toolsets.txt.
vm_image_paths() {
	local toolset
	printf 'images/%s/*\n' "$1"
	while read -r toolset; do
		printf 'images/toolsets/%s/*\n' "$toolset"
	done < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "images/$1/toolsets.txt" 2>/dev/null)
}

# What every Compute Engine image is built from, besides its own paths.
readonly every_vm=(
	"images/shared/*" "images/toolsets/install-toolset.sh" "images/toolsets/write-claude-md.sh"
	"images/toolsets/toolset-checks.sh" "images/toolsets/write-versions.sh" "images/toolsets/skills/*"
	"infra/gcp/project.env"
)

# A scheduled run rebuilds its class's types in full and nothing else; the
# orchestrator (Claude Code, the hooks) rides the nightly.
if [[ -n "$schedule" ]]; then
	jq -n --argjson types "$(for type in "${types[@]}"; do
		[[ "$(gcp_rebuild_class "$type")" == "$schedule" ]] || continue
		jq -cn --arg type "$type" --arg backend "$(gcp_backend "$type")" '{type: $type, backend: $backend}'
	done | jq -cs .)" --argjson orchestrator "$([[ "$schedule" == nightly ]] && echo true || echo false)" '
		{types: [$types[] | select(.backend == "run") | {type, build: true}],
		 vms: [$types[] | select(.backend == "compute") | {type}],
		 orchestrator: $orchestrator, prune: false}'
	exit 0
fi

entries=()
vms=()
for type in "${types[@]}"; do
	if [[ "$(gcp_backend "$type")" == compute ]]; then
		mapfile -t own < <(vm_image_paths "$type")
		if $all || touched "${every_vm[@]}" "${own[@]}"; then
			vms+=("$(jq -cn --arg type "$type" '{type: $type}')")
		fi
		continue
	fi
	mapfile -t own < <(cloud_image_paths "$type")
	if $all || touched "${every_type[@]}" "${own[@]}"; then
		entries+=("$(jq -cn --arg type "$type" '{type: $type, build: true}')")
	elif touched "infra/gcp/$type.env" "scripts/cloud/job-spec.sh"; then
		entries+=("$(jq -cn --arg type "$type" '{type: $type, build: false}')")
	fi
done

orchestrator=false
if $all || touched "hooks/gcp/*" "images/cloud/orchestrator/*" "infra/gcp/*.env" \
	"images/cloud/docker-bake.hcl" ".dockerignore"; then
	orchestrator=true
fi

prune=false
if $all || ((${#deleted[@]} > 0)); then
	prune=true
fi

vms_json="$(printf '%s\n' "${vms[@]}" | jq -cs 'map(select(. != null))')"
printf '%s\n' "${entries[@]}" | jq -cs --argjson vms "$vms_json" --argjson orchestrator "$orchestrator" \
	--argjson prune "$prune" '{types: map(select(. != null)), vms: $vms, orchestrator: $orchestrator, prune: $prune}'
