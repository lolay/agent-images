#!/bin/bash
# The 3d toolset's own steps (images/toolsets/3d), run by install-toolset.sh at
# image build after the packages and the Brewfile (uv), where there's no GPU:
# nothing here may need one.
#
# - COLMAP's runtime libraries: the builder stage (images/cloud/3d-gpu) built
#   COLMAP into /opt/colmap and listed the Ubuntu packages its binary links:
#   name=version for those its build list installed (locked through
#   build-packages.txt), the name alone for those the base image had.
# - Blender from blender.org (BLENDER_VERSION), checked against the
#   release's SHA-256 file, in /opt/blender: its own Python and Cycles kernels.
# - /opt/3d: Python and the locked environment (pyproject.toml, uv.lock), with
#   gsplat's CUDA extension compiled here for every GPU in TORCH_CUDA_ARCH_LIST
#   (base-gpu's), so no session compiles it.
#
# log, die and install_apt are install-toolset.sh's.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly here
readonly root=/opt/3d
readonly colmap_packages=/opt/colmap/share/colmap/runtime-packages.txt
: "${TORCH_CUDA_ARCH_LIST:?the GPU types build on base-gpu, which sets TORCH_CUDA_ARCH_LIST (images/cloud/docker-bake.hcl)}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[[ -s "$colmap_packages" ]] || die "no $colmap_packages: the builder stage's COLMAP isn't in this image"
log "COLMAP's runtime libraries: $(paste -sd ' ' "$colmap_packages")"
install_apt "$colmap_packages"
/opt/colmap/bin/colmap -h >/dev/null || die "colmap doesn't run with those libraries"

# BLENDER_VERSION: a version (the lock's, scripts/cloud/lock.sh, or a pin), else
# the newest in blender.org's release index. blender.sh knows where the files
# come from (blender.org, then its mirrors) and says which served each.
version="${BLENDER_VERSION:-latest}"
if [[ "$version" == latest ]]; then
	version="$(bash "$here/blender.sh" latest)"
fi
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "unexpected Blender release '$version'"
series="${version%.*}"
log "Blender $version"
bash "$here/blender.sh" get "Blender$series/blender-$version-linux-x64.tar.xz" "$work/blender.tar.xz"
bash "$here/blender.sh" get "Blender$series/blender-$version.sha256" "$work/blender.sha256"
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
log "compiling gsplat for $TORCH_CUDA_ARCH_LIST"
CUDA_HOME=/usr/local/cuda PATH="/usr/local/cuda/bin:$PATH" \
	MAX_JOBS="$(nproc)" uv pip install --python "$root/.venv" --no-build-isolation --no-deps "$work/gsplat" 2>&1
uv cache clean 2>&1
# The extension has to be in the environment, built, not left for a session's
# first import to compile (gsplat falls back to JIT when it's missing).
compgen -G "$root/.venv/lib/python3*/site-packages/gsplat/csrc*.so" >/dev/null ||
	die "gsplat's CUDA extension wasn't built"
