#!/bin/bash
# Builds COLMAP into /opt/colmap: run by images/cloud/3d-gpu's builder stage,
# whose install tree is all the image keeps. CUDA for every GPU in CUDAARCHS
# (base-gpu's); headless, so SIFT runs on CUDA rather than OpenGL.
# Its Ubuntu packages are build-packages.txt beside it, which the lock resolves.
# It ends by listing the packages the binary links in runtime-packages.txt,
# which the 3d toolset's install.sh installs: those the build list installed at
# the version installed here (so the image runs COLMAP with what it was built
# against, pins included), and those the base image already had by name, since
# a later toolset may move them within the snapshot.
#
# log, die and install_apt are install-toolset.sh's, which the stage mounts
# beside the toolset.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly here
readonly prefix=/opt/colmap
readonly src=/tmp/colmap

TOOLSET_LOG_NAME=build-colmap
# shellcheck source=images/toolsets/install-toolset.sh
source "$here/../install-toolset.sh"
: "${CUDAARCHS:?the GPU types build on base-gpu, which sets CUDAARCHS (images/cloud/docker-bake.hcl)}"

# COLMAP_VERSION: a release tag (the lock's, or a pin), else the newest release:
# the tag GitHub redirects /releases/latest to (no API call, no rate limit).
version="${COLMAP_VERSION:-latest}"
if [[ "$version" == latest ]]; then
	latest="$(curl -fsSLI --retry 3 -o /dev/null -w '%{url_effective}' https://github.com/colmap/colmap/releases/latest)"
	version="${latest##*/}"
fi
[[ "$version" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "unexpected COLMAP release '$version'"

# What the install adds or changes: the build list's packages and dependencies.
# shellcheck disable=SC2016 # dpkg-query's own ${field} syntax, not the shell's
installed() { dpkg-query -W -f='${Package}=${Version}\n' | LC_ALL=C sort; }
before="$(installed)"
install_apt "$here/build-packages.txt"
from_build_list="$(LC_ALL=C comm -13 <(printf '%s\n' "$before") <(installed))"
# Ubuntu's OpenImageIO CMake config wants OpenCV's include directory to exist,
# though COLMAP uses none of it (the install guide's workaround).
sudo mkdir -p /usr/include/opencv4

log "COLMAP $version"
mkdir -p "$src"
curl -fsSL --retry 3 "https://github.com/colmap/colmap/archive/refs/tags/$version.tar.gz" |
	tar -xz -C "$src" --strip-components=1
# STD hash maps: the default, and the ABI pycolmap's wheels use.
cmake -S "$src" -B "$src/build" -GNinja \
	-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
	-DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc -DCUDA_ENABLED=ON \
	-DCMAKE_CUDA_ARCHITECTURES="$CUDAARCHS" \
	-DGUI_ENABLED=OFF -DOPENGL_ENABLED=OFF -DONNX_ENABLED=OFF \
	-DTESTS_ENABLED=OFF -DCCACHE_ENABLED=OFF -DCOLMAP_HASH_MAP_BACKEND=STD
cmake --build "$src/build"
sudo cmake --install "$src/build"
rm -rf "$src"

bin="$prefix/bin/colmap"
if ldd "$bin" | grep -q 'not found'; then
	ldd "$bin" >&2
	die "colmap links a library that isn't installed"
fi
# The package that owns each library: dpkg records some under /usr/lib and
# some under /lib, and ldd's path can be either.
owner() {
	local real
	real="$(readlink -f "$1")"
	dpkg -S "$real" 2>/dev/null || dpkg -S "${real#/usr}" 2>/dev/null ||
		die "no package owns $1 ($real)"
}
list="$prefix/share/colmap/runtime-packages.txt"
sudo install -d -m 755 "$(dirname "$list")"
# Through a file, not <(...): owner's die has to stop the build (pipefail).
owners_file="$(mktemp)"
ldd "$bin" | awk '/=> \// {print $3}' | while read -r lib; do
	owner "$lib"
done | cut -d: -f1 | LC_ALL=C sort -u >"$owners_file"
mapfile -t owners <"$owners_file"
rm -f "$owners_file"
((${#owners[@]} > 0)) || die "found no packages for $bin's libraries"
# Pinned to the built version if the build list installed it; by name if the
# base image had it (that version may not be in the snapshot at all).
for package in "${owners[@]}"; do
	built="$(awk -F= -v name="$package" '$1 == name {print; exit}' <<<"$from_build_list")"
	printf '%s\n' "${built:-$package}"
done | sudo tee "$list" >/dev/null
log "runtime packages: $(paste -sd ' ' "$list")"
