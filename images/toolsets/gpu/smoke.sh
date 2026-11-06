#!/bin/bash
# smoke: live (needs a GPU)
# The gpu toolset's smoke test (specs/gcp.md, m4): the driver answers, a CUDA
# kernel runs on the device (agent-cuda-probe, built with the image), and which
# driver libraries the host mounted. EGL and Vulkan are reported, not failed:
# whether they reach the GPU on Cloud Run is m3's open question.
set -euo pipefail

gpu="$(nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader)"
echo "gpu: $gpu"
agent-cuda-probe
agent-gpu-libs
egl="$(eglinfo -B -p surfaceless 2>/dev/null | sed -n 's/^OpenGL core profile renderer: //p' || true)"
vulkan="$(vulkaninfo --summary 2>/dev/null | sed -n 's/^[[:space:]]*deviceName *= //p' || true)"
egl="${egl%%$'\n'*}" vulkan="${vulkan%%$'\n'*}"
echo "gpu: ${gpu%%,*} ran a CUDA kernel; EGL on ${egl:-nothing}; Vulkan on ${vulkan:-nothing}"
