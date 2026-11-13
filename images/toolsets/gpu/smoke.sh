#!/bin/bash
# smoke: live (needs a GPU)
# The gpu toolset's smoke test (specs/gcp.md, m4): the driver answers, a CUDA
# kernel runs on the device (agent-cuda-probe, built with the image), and which
# driver libraries the host mounted. EGL, Vulkan and NVENC are reported, not
# failed: the linux-gpu session test found all three on the GPU (2026-11-13), but
# the smoke gates every deploy, and a driver mount without them is a finding to
# read, not a reason to keep the last image.
set -euo pipefail

gpu="$(nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader)"
echo "gpu: $gpu"
agent-cuda-probe
agent-gpu-libs
egl="$(eglinfo -B -p surfaceless 2>/dev/null | sed -n 's/^OpenGL core profile renderer: //p' || true)"
vulkan="$(vulkaninfo --summary 2>/dev/null | sed -n 's/^[[:space:]]*deviceName *= //p' || true)"
egl="${egl%%$'\n'*}" vulkan="${vulkan%%$'\n'*}"
nvenc=no
if ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=640x360:rate=30 \
	-frames:v 30 -c:v h264_nvenc -f null - 2>/dev/null; then
	nvenc=yes
fi
echo "gpu: ${gpu%%,*} ran a CUDA kernel; EGL on ${egl:-nothing}; Vulkan on ${vulkan:-nothing}; NVENC encodes: $nvenc"
