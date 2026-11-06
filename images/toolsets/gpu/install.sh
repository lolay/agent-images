#!/bin/bash
# The gpu toolset's own steps (images/toolsets/gpu), run by install-toolset.sh at
# image build, where there's no GPU: nothing here may need one.
#
# - VirtualGL, newest release, from its GitHub release (it isn't in Ubuntu).
# - Vendor files that point GLVND's EGL and Vulkan's loader at NVIDIA's libraries
#   where Cloud Run mounts them. Docker's NVIDIA runtime writes these itself;
#   Cloud Run documents only the library mount, so the image carries them. If the
#   mount has no graphics libraries, both fall back to Mesa (llvmpipe, lavapipe).
# - agent-cuda-probe, the smoke check's CUDA kernel, for the L4 and Blackwell,
#   and agent-gpu-libs, which reports which driver libraries the host mounted.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly here
readonly nvidia_lib=/usr/local/nvidia/lib64

log() { printf '==> gpu: %s\n' "$*"; }
apt() { sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get -q "$@"; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# VirtualGL: the latest release's tag, from the redirect GitHub gives for
# /releases/latest (no API call, so no rate limit), then its amd64 .deb.
latest="$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/VirtualGL/virtualgl/releases/latest)"
version="${latest##*/}"
[[ "$version" =~ ^[0-9]+(\.[0-9]+)+$ ]] || {
	printf '==> gpu: unexpected VirtualGL release %s\n' "$latest" >&2
	exit 1
}
log "VirtualGL $version"
curl -fsSL -o "$work/virtualgl.deb" \
	"https://github.com/VirtualGL/virtualgl/releases/download/$version/virtualgl_${version}_amd64.deb"
apt update
apt install -y --no-install-recommends "$work/virtualgl.deb"
apt clean
if [[ "${APT_DROP_LISTS:-false}" == "true" ]]; then
	sudo rm -rf /var/lib/apt/lists/*
fi

# GLVND tries EGL vendors in file-name order and skips one whose library won't
# load, so 10_nvidia comes before Mesa's 50_mesa. Full paths: ldconfig's cache
# is built here, before the driver exists, and the session can't rerun it.
sudo install -d -m 755 /usr/share/glvnd/egl_vendor.d /usr/share/vulkan/icd.d
printf '{\n  "file_format_version": "1.0.0",\n  "ICD": {"library_path": "%s/libEGL_nvidia.so.0"}\n}\n' \
	"$nvidia_lib" | sudo tee /usr/share/glvnd/egl_vendor.d/10_nvidia.json >/dev/null
# NVIDIA's Vulkan driver is its GLX library.
printf '{\n  "file_format_version": "1.0.1",\n  "ICD": {"library_path": "%s/libGLX_nvidia.so.0", "api_version": "1.4.0"}\n}\n' \
	"$nvidia_lib" | sudo tee /usr/share/vulkan/icd.d/nvidia_icd.json >/dev/null

# The probe: SASS for the L4 (sm_89) and Blackwell (sm_120), and PTX for
# compute_120 so a newer GPU can still JIT it.
log "building agent-cuda-probe"
/usr/local/cuda/bin/nvcc -O2 \
	-gencode arch=compute_89,code=sm_89 \
	-gencode arch=compute_120,code=sm_120 \
	-gencode arch=compute_120,code=compute_120 \
	-o "$work/agent-cuda-probe" "$here/cuda-probe.cu"
sudo install -m 755 "$work/agent-cuda-probe" /usr/local/bin/agent-cuda-probe
sudo install -m 755 "$here/agent-gpu-libs.sh" /usr/local/bin/agent-gpu-libs
