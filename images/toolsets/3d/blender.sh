#!/bin/bash
# Blender's release files: the one place that knows where they come from and how
# its index reads. Two callers: the 3d toolset's install.sh at image build (in
# the build container, from /tmp/toolsets/3d) and scripts/cloud/lock.sh's
# resolve and pins (from the repo root).
#
#   blender.sh latest            the newest x.y.z with a Linux x64 build: the
#                                newest BlenderX.Y/ series in the release index,
#                                then the newest build in it
#   blender.sh get <path> [out]  one file under the release tree (Blender5.3/,
#                                Blender5.3/blender-5.3.0.sha256), to out or stdout
#
# Each file is tried at download.blender.org, then at Blender's official mirrors,
# in order. download.blender.org answers 403 to GitHub's hosted runners now and
# then (lock-watch run 43715897836, cloud-build job 132659831715, 2026-11-15),
# and curl's --retry leaves a 403 alone, so each source gets --retry-all-errors.
# Every file says on stderr which source served it; nothing falls back silently.
# A mirror only ever stands in for the same path, and install.sh checks the
# tarball against the release's SHA-256 file wherever each came from.
set -euo pipefail
shopt -s inherit_errexit

readonly sources=(
	https://download.blender.org/release
	https://mirrors.ocf.berkeley.edu/blender/release
	https://ftp.nluug.nl/pub/graphics/blender/release
)

die() {
	printf 'blender: %s\n' "$*" >&2
	exit 1
}

# get <path> [out]: the first source that serves <path>. A source's own retries
# end before the next is tried, so a mirror only answers when the primary
# refused every attempt. Every transfer goes to a temporary file, which curl
# starts afresh each time, and only the one that succeeded reaches <out> or
# stdout: a transfer that broke off partway can't leave its bytes in front of
# the next source's body.
get() {
	local path="$1" out="${2:-}" source part
	part="$(mktemp)"
	for source in "${sources[@]}"; do
		if curl -fsSL --retry 3 --retry-all-errors -o "$part" "$source/$path"; then
			printf 'blender: %s from %s\n' "${path:-the release index}" "$source" >&2
			if [[ -n "$out" ]]; then mv "$part" "$out"; else cat "$part" && rm -f "$part"; fi
			return 0
		fi
		printf 'blender: %s/%s failed\n' "$source" "$path" >&2
	done
	rm -f "$part"
	die "no source served ${path:-the release index} (${sources[*]})"
}

latest() {
	local index series version
	index="$(get "")"
	series="$(sed -n -E 's/.*href="Blender([0-9]+\.[0-9]+)\/".*/\1/p' <<<"$index" | sort -V | tail -n 1)"
	[[ -n "$series" ]] || die "no Blender series in the release index"
	index="$(get "Blender$series/")"
	version="$(sed -n -E 's/.*href="blender-([0-9]+\.[0-9]+\.[0-9]+)-linux-x64\.tar\.xz".*/\1/p' <<<"$index" | sort -V | tail -n 1)"
	[[ -n "$version" ]] || die "no Linux build in Blender$series/"
	printf '%s\n' "$version"
}

case "${1:-}" in
latest)
	(($# == 1)) || die "usage: blender.sh latest | get <path> [out]"
	latest
	;;
get)
	(($# == 2 || $# == 3)) || die "usage: blender.sh latest | get <path> [out]"
	get "$2" "${3:-}"
	;;
*) die "usage: blender.sh latest | get <path> [out]" ;;
esac
