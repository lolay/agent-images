#!/bin/bash
# write-versions.sh: what an image contains, as Markdown on stdout (specs/gcp.md,
# m4 "Versions report"). The cloud runner Dockerfile writes it to
# /etc/agent-images/versions.md in its last layer, and the android image's
# provision.sh at the end of its build; the workflows put it in their summaries.
#
# Usage: write-versions.sh <type> <commit>
#
# A section whose tool isn't installed is left out. Progress and a tool's own
# complaints go to stdout with its section, never stderr: the builds must stay
# green.
#
#   AGENT_TOOLSET_CHECKS   the binary checks' runner (default agent-toolset-checks)
#   AGENT_GIS_MANIFEST     gis's pixi manifest (default /opt/gis/pixi.toml)
set -euo pipefail

(($# == 2)) || {
	echo "usage: write-versions.sh <type> <commit>" >&2
	exit 2
}
readonly type="$1" commit="$2"
readonly checks="${AGENT_TOOLSET_CHECKS:-agent-toolset-checks}"
readonly gis_manifest="${AGENT_GIS_MANIFEST:-/opt/gis/pixi.toml}"

readonly fence='```'

has() { command -v "$1" >/dev/null 2>&1; }

# block <title> <command>...: a section with the command's output in a code block,
# and a note if it failed (its output still says what it could).
block() {
	local title="$1" output status=0
	shift
	output="$("$@" 2>&1 </dev/null)" || status=$?
	printf '\n## %s\n\n%stext\n%s\n%s\n' "$title" "$fence" "$output" "$fence"
	((status == 0)) || printf '\n%s exited %d.\n' "$*" "$status"
}

printf '# agent-%s\n\n' "$type"
printf -- '- Built: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf -- '- Commit: %s\n' "$commit"
if has claude; then
	printf -- '- Claude Code: %s\n' "$(claude --version 2>&1 | head -n 1)"
fi

if has "$checks"; then
	# At build time a check that needs the runtime fails here; it's recorded as is.
	block "Binary checks" "$checks" versions
fi
if has brew; then
	block "Homebrew (with dependencies)" brew list --versions
fi
if has rustup; then
	block "Rust toolchains" rustup toolchain list
fi
if has tenv; then
	block "Terraform (tenv)" tenv tf list
fi
if has pixi && [[ -f "$gis_manifest" ]]; then
	block "pixi environment ($(dirname "$gis_manifest"))" pixi list --manifest-path "$gis_manifest"
fi
if has dpkg-query; then
	apt_status=0
	packages="$(dpkg-query -W -f '${Package} ${Version}\n' 2>&1)" || apt_status=$?
	printf '\n## apt\n\n<details>\n<summary>%d packages</summary>\n\n%stext\n%s\n%s\n\n</details>\n' \
		"$(wc -l <<<"$packages")" "$fence" "$packages" "$fence"
	((apt_status == 0)) || printf '\ndpkg-query exited %d.\n' "$apt_status"
fi
