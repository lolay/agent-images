# shellcheck shell=bash
# What a Cloud Run type is built from, read from its Dockerfiles: the build
# parent chain and the toolsets each one installs. Sourced by changed-targets.sh
# (what a push redeploys) and anything else that needs a type's toolsets, so the
# Dockerfile parsing lives here once. Run from the repo root. Sources
# hooks/gcp/lib.sh for the per-type settings (gcp_backend, gcp_rebuild_class).

# shellcheck source=hooks/gcp/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../../hooks/gcp/lib.sh"

# cloud_parent_of <dir>: the build parent's directory under images/cloud that a
# Dockerfile names (FROM agent-<parent>), if any. agent-base-gpu has no
# directory of its own: it's bake target base-gpu, base's Dockerfile on CUDA
# (docker-bake.hcl), so its directory is base. test_bake.py checks this chain
# against the bake file's.
cloud_parent_of() {
	sed -n -E 's/^FROM[[:space:]]+agent-([a-z0-9-]+).*/\1/p' "images/cloud/$1/Dockerfile" 2>/dev/null |
		head -n 1 | sed 's/^base-gpu$/base/'
}

# cloud_toolsets_of <dir>: the toolsets one Dockerfile installs (its
# `install-toolset.sh <name>` lines), in order.
cloud_toolsets_of() {
	sed -n -E 's/.*install-toolset\.sh[[:space:]]+([a-z0-9-]+)[[:space:]]*$/\1/p' "images/cloud/$1/Dockerfile" 2>/dev/null
}

# cloud_chain <type>: the type's directory, then each build parent's, up to the
# root.
cloud_chain() {
	local dir="$1" seen=" "
	while [[ -n "$dir" && "$seen" != *" $dir "* ]]; do
		printf '%s\n' "$dir"
		seen+="$dir "
		dir="$(cloud_parent_of "$dir")"
	done
}

# cloud_toolsets <type>: every toolset the type's image installs, in install
# order (the root parent's first).
cloud_toolsets() {
	local dir
	while read -r dir; do
		cloud_toolsets_of "$dir"
	done < <(cloud_chain "$1" | tac)
}

# A parent's pattern below uses !(...), which needs extglob when it is matched.
shopt -s extglob

# cloud_image_paths <type>: glob patterns for every path the type's image is
# built from: its directory and each build parent's, and the toolsets each of
# them installs. A type builds from its own versions.lock (lock.sh bake-args)
# and no Dockerfile copies one, so a parent's lock is not an input: a linux
# lock bump must not rebuild linux-gpu.
cloud_image_paths() {
	local dir toolset first=true
	while read -r dir; do
		if $first; then
			printf 'images/cloud/%s/*\n' "$dir"
			first=false
		else
			printf 'images/cloud/%s/!(versions.lock)\n' "$dir"
		fi
		while read -r toolset; do
			printf 'images/toolsets/%s/*\n' "$toolset"
		done < <(cloud_toolsets_of "$dir")
	done < <(cloud_chain "$1")
}
