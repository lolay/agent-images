#!/bin/bash
# Builds COLMAP into /opt/colmap: run by images/cloud/3d-gpu's builder stage,
# whose install tree is all the image keeps. CUDA for the L4 (8.9) and
# Blackwell (12.0, with PTX); headless, so SIFT runs on CUDA rather than OpenGL.
# It ends by listing the Ubuntu packages the binary links in
# runtime-packages.txt, which the 3d toolset's install.sh installs.
set -euo pipefail

readonly prefix=/opt/colmap
readonly src=/tmp/colmap

log() { printf '==> build-colmap: %s\n' "$*"; }
die() {
	printf '==> build-colmap: %s\n' "$*" >&2
	exit 1
}

# COLMAP_VERSION: a release tag (the lock's, or a pin), else the newest release:
# the tag GitHub redirects /releases/latest to (no API call, no rate limit).
version="${COLMAP_VERSION:-latest}"
if [[ "$version" == latest ]]; then
	latest="$(curl -fsSLI --retry 3 -o /dev/null -w '%{url_effective}' https://github.com/colmap/colmap/releases/latest)"
	version="${latest##*/}"
fi
[[ "$version" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "unexpected COLMAP release '$version'"

# COLMAP's install guide's Ubuntu list, less Qt and the tests'. Keep the rest:
# configure finds OpenGL and GLEW as REQUIRED even with the GUI and OpenGL off
# (cmake/FindDependencies.cmake), and Ubuntu's OpenImageIO CMake config imports
# openimageio-tools' binaries. gflags and the GL headers aren't in the guide.
sudo apt-get update -q
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -q -y --no-install-recommends \
	cmake ninja-build build-essential git \
	libboost-program-options-dev libboost-graph-dev libboost-system-dev \
	libeigen3-dev libopenimageio-dev openimageio-tools libmetis-dev libgoogle-glog-dev \
	libsqlite3-dev libcgal-dev libceres-dev libsuitesparse-dev \
	libcurl4-openssl-dev libssl-dev libopenblas-dev libgflags-dev \
	libgl-dev libopengl-dev libglx-dev libglew-dev
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
	-DCMAKE_CUDA_ARCHITECTURES="89;120" \
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
while read -r lib; do
	owner "$lib"
done < <(ldd "$bin" | awk '/=> \// {print $3}') |
	cut -d: -f1 | LC_ALL=C sort -u | sudo tee "$list" >/dev/null
log "runtime packages: $(paste -sd ' ' "$list")"
