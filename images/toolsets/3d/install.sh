#!/bin/bash
# The 3d toolset's own steps (images/toolsets/3d), run by install-toolset.sh at
# image build after the packages and the Brewfile (uv), where there's no GPU:
# nothing here may need one.
#
# - COLMAP's runtime libraries: the builder stage (images/cloud/3d-gpu) built
#   COLMAP into /opt/colmap and listed the Ubuntu packages its binary links.
# - Blender from blender.org (BLENDER_VERSION), checked against the
#   release's SHA-256 file, in /opt/blender: its own Python and Cycles kernels.
# - /opt/3d: Python and the locked environment (pyproject.toml, uv.lock), with
#   gsplat's CUDA extension compiled here for the L4 (8.9) and Blackwell (12.0,
#   plus PTX), so no session compiles it.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly here
readonly root=/opt/3d
readonly colmap_packages=/opt/colmap/share/colmap/runtime-packages.txt

log() { printf '==> 3d: %s\n' "$*"; }
die() {
	printf '==> 3d: %s\n' "$*" >&2
	exit 1
}
apt() { sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get -q "$@"; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[[ -s "$colmap_packages" ]] || die "no $colmap_packages: the builder stage's COLMAP isn't in this image"
mapfile -t packages <"$colmap_packages"
log "COLMAP's runtime libraries: ${packages[*]}"
apt update
apt install -y --no-install-recommends "${packages[@]}"
apt clean
if [[ "${APT_DROP_LISTS:-false}" == "true" ]]; then
	sudo rm -rf /var/lib/apt/lists/*
fi
/opt/colmap/bin/colmap -h >/dev/null || die "colmap doesn't run with those libraries"

# BLENDER_VERSION: a version (the lock's, scripts/cloud/lock.sh, or a pin), else
# the newest in blender.org's release index.
version="${BLENDER_VERSION:-latest}"
if [[ "$version" == latest ]]; then
	series="$(curl -fsSL --retry 3 https://download.blender.org/release/ |
		sed -n -E 's/.*href="Blender([0-9]+\.[0-9]+)\/".*/\1/p' | sort -V | tail -n 1)"
	version="$(curl -fsSL --retry 3 "https://download.blender.org/release/Blender$series/" |
		sed -n -E 's/.*href="blender-([0-9]+\.[0-9]+\.[0-9]+)-linux-x64\.tar\.xz".*/\1/p' | sort -V | tail -n 1)"
fi
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "unexpected Blender release '$version'"
series="${version%.*}"
url="https://download.blender.org/release/Blender$series"
log "Blender $version"
curl -fsSL --retry 3 -o "$work/blender.tar.xz" "$url/blender-$version-linux-x64.tar.xz"
curl -fsSL --retry 3 -o "$work/blender.sha256" "$url/blender-$version.sha256"
sum="$(awk -v f="blender-$version-linux-x64.tar.xz" '$2 == f {print $1}' "$work/blender.sha256")"
[[ -n "$sum" ]] || die "no checksum for blender-$version-linux-x64.tar.xz in blender-$version.sha256"
printf '%s  %s\n' "$sum" "$work/blender.tar.xz" | sha256sum -c --quiet -
sudo install -d -m 755 /opt/blender
sudo tar -xJf "$work/blender.tar.xz" -C /opt/blender --strip-components=1
sudo ln -sf /opt/blender/blender /usr/local/bin/blender

log "Python and the locked environment in $root"
sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 755 "$root"
install -m 644 "$here/pyproject.toml" "$here/uv.lock" "$root/"
export UV_PYTHON_INSTALL_DIR="$root/python" UV_PROJECT_ENVIRONMENT="$root/.venv" UV_LINK_MODE=copy
cd "$root"
# Everything but gsplat first: its build imports torch (no-build-isolation-package).
uv sync --frozen --no-install-package gsplat 2>&1

# gsplat from the sdist uv.lock names, checked against the lock's hash, with one
# change: gsplat 1.5.3 compiles its CUDA code as C++17, and PyTorch 2.14's
# headers need C++20 (std::strong_ordering; cloud-build run 43526979224).
read -r url hash < <(
	"$root/.venv/bin/python" - "$root/uv.lock" <<'PY'
import sys, tomllib
lock = tomllib.load(open(sys.argv[1], "rb"))
sdist = next(p["sdist"] for p in lock["package"] if p["name"] == "gsplat")
print(sdist["url"], sdist["hash"].removeprefix("sha256:"))
PY
)
curl -fsSL --retry 3 -o "$work/gsplat.tar.gz" "$url"
printf '%s  %s\n' "$hash" "$work/gsplat.tar.gz" | sha256sum -c --quiet -
mkdir "$work/gsplat"
tar -xzf "$work/gsplat.tar.gz" -C "$work/gsplat" --strip-components=1
grep -q '"-std=c++17"' "$work/gsplat/setup.py" || die "gsplat's setup.py no longer sets -std=c++17: drop the patch"
sed -i 's/"-std=c++17"/"-std=c++20"/' "$work/gsplat/setup.py"
log "compiling gsplat for compute capability 8.9 and 12.0"
CUDA_HOME=/usr/local/cuda PATH="/usr/local/cuda/bin:$PATH" TORCH_CUDA_ARCH_LIST="8.9;12.0+PTX" \
	MAX_JOBS="$(nproc)" uv pip install --python "$root/.venv" --no-build-isolation --no-deps "$work/gsplat" 2>&1
uv cache clean 2>&1
# The extension has to be in the environment, built, not left for a session's
# first import to compile (gsplat falls back to JIT when it's missing).
compgen -G "$root/.venv/lib/python3*/site-packages/gsplat/csrc*.so" >/dev/null ||
	die "gsplat's CUDA extension wasn't built"
