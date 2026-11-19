#!/bin/bash
# The gpu toolset's own steps (images/toolsets/gpu), run by install-toolset.sh at
# image build, where there's no GPU: nothing here may need one.
#
# - VirtualGL from its GitHub release (it isn't in Ubuntu): VIRTUALGL_VERSION, a
#   version (the lock's, scripts/cloud/lock.sh, or a pin), else the newest.
# - Vendor files that point GLVND's EGL and Vulkan's loader at NVIDIA's libraries
#   where Cloud Run mounts them. Docker's NVIDIA runtime writes these itself;
#   Cloud Run documents only the library mount, so the image carries them. If the
#   mount has no graphics libraries, both fall back to Mesa (llvmpipe, lavapipe).
# - agent-cuda-probe, the smoke check's CUDA kernel, for every GPU in CUDAARCHS
#   (base-gpu's), and agent-gpu-libs, which reports which driver libraries the
#   host mounted.
#
# log, die and install_apt_packages are install-toolset.sh's.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly here
readonly nvidia_lib=/usr/local/nvidia/lib64
: "${CUDAARCHS:?the GPU types build on base-gpu, which sets CUDAARCHS (images/cloud/docker-bake.hcl)}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# VirtualGL's amd64 .deb for that version. latest is the tag GitHub redirects
# /releases/latest to (no API call, so no rate limit).
version="${VIRTUALGL_VERSION:-latest}"
# Where its releases are, for the downloads here and for the lock, which reads
# it here (scripts/cloud/lock.sh, release_tools).
readonly VIRTUALGL_RELEASES=https://github.com/VirtualGL/virtualgl/releases
if [[ "$version" == latest ]]; then
	latest="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$VIRTUALGL_RELEASES/latest")"
	version="${latest##*/}"
fi
[[ "$version" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "unexpected VirtualGL release $version"
log "VirtualGL $version"
curl -fsSL -o "$work/virtualgl.deb" \
	"$VIRTUALGL_RELEASES/download/$version/virtualgl_${version}_amd64.deb"
install_apt_packages "$work/virtualgl.deb"

# GLVND tries EGL vendors in file-name order and skips one whose library won't
# load, so 10_nvidia comes before Mesa's 50_mesa. Full paths: ldconfig's cache
# is built here, before the driver exists, and the session can't rerun it.
sudo install -d -m 755 /usr/share/glvnd/egl_vendor.d /usr/share/vulkan/icd.d
printf '{\n  "file_format_version": "1.0.0",\n  "ICD": {"library_path": "%s/libEGL_nvidia.so.0"}\n}\n' \
	"$nvidia_lib" | sudo tee /usr/share/glvnd/egl_vendor.d/10_nvidia.json >/dev/null
# NVIDIA's Vulkan driver is its GLX library.
printf '{\n  "file_format_version": "1.0.1",\n  "ICD": {"library_path": "%s/libGLX_nvidia.so.0", "api_version": "1.4.0"}\n}\n' \
	"$nvidia_lib" | sudo tee /usr/share/vulkan/icd.d/nvidia_icd.json >/dev/null

# The probe, for CUDAARCHS in nvcc's terms: <cc>-real is SASS for that GPU,
# <cc>-virtual PTX, and a plain <cc> both, so a newer GPU can still JIT it.
gencode=()
IFS=';' read -ra archs <<<"$CUDAARCHS"
for arch in "${archs[@]}"; do
	cc="${arch%-*}"
	[[ "$cc" =~ ^[0-9]+$ ]] || die "unexpected CUDAARCHS entry '$arch' in '$CUDAARCHS'"
	[[ "$arch" == *-virtual ]] || gencode+=(-gencode "arch=compute_$cc,code=sm_$cc")
	[[ "$arch" == *-real ]] || gencode+=(-gencode "arch=compute_$cc,code=compute_$cc")
done
log "building agent-cuda-probe: ${gencode[*]}"
/usr/local/cuda/bin/nvcc -O2 "${gencode[@]}" -o "$work/agent-cuda-probe" "$here/cuda-probe.cu"
sudo install -m 755 "$work/agent-cuda-probe" /usr/local/bin/agent-cuda-probe
sudo install -m 755 "$here/agent-gpu-libs.sh" /usr/local/bin/agent-gpu-libs
