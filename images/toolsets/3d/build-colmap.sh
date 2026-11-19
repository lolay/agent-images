#!/bin/bash
# Builds COLMAP into /opt/colmap: run by images/cloud/3d-gpu's builder stage,
# whose install tree is all the image keeps. CUDA for every GPU in CUDAARCHS
# (base-gpu's); headless, so SIFT runs on CUDA rather than OpenGL.
# Its Ubuntu packages are build-packages.txt beside it, which the lock resolves.
# It ends by listing the packages the binary links in runtime-packages.txt
# (runtime-packages.sh beside it), which the 3d toolset's install.sh installs.
#
# log, die, install_apt and github_latest are install-toolset.sh's, which the
# stage mounts beside the toolset.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly here
readonly prefix=/opt/colmap
readonly src=/tmp/colmap

TOOLSET_LOG_NAME=build-colmap
# shellcheck source=images/toolsets/install-toolset.sh
source "$here/../install-toolset.sh"
# shellcheck source=images/toolsets/3d/runtime-packages.sh
source "$here/runtime-packages.sh"
: "${CUDAARCHS:?the GPU types build on base-gpu, which sets CUDAARCHS (images/cloud/docker-bake.hcl)}"

# COLMAP_VERSION: a release tag (the lock's, or a pin), else the newest release.
version="${COLMAP_VERSION:-latest}"
# Where its releases are, for the downloads here and for the lock, which reads
# it here (scripts/cloud/lock.sh, release_tools).
readonly COLMAP_RELEASES=https://github.com/colmap/colmap/releases
if [[ "$version" == latest ]]; then
	version="$(github_latest "$COLMAP_RELEASES")"
fi
[[ "$version" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "unexpected COLMAP release '$version'"

# What the install adds or changes: the build list's packages and dependencies.
before="$(installed_packages)"
grep -qvE '^[[:space:]]*(#|$)' "$here/build-packages.txt" || die "no packages in $here/build-packages.txt"
install_apt "$here/build-packages.txt"
from_build_list="$(packages_changed_since "$before")"
# Ubuntu's OpenImageIO CMake config wants OpenCV's include directory to exist,
# though COLMAP uses none of it (the install guide's workaround).
sudo mkdir -p /usr/include/opencv4

log "COLMAP $version"
mkdir -p "$src"
curl -fsSL --retry 3 "${COLMAP_RELEASES%/releases}/archive/refs/tags/$version.tar.gz" |
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
write_runtime_packages "$bin" "$prefix/share/colmap/runtime-packages.txt" "$from_build_list"
