- One NVIDIA GPU (`nvidia-smi`, `nvtop`) and CUDA 13.0's toolkit (`nvcc`,
  `$CUDA_HOME`), matching the host's driver. Build for the GPU you're on: `sm_120`
  on an RTX PRO 6000, `sm_89` on an L4 (`nvidia-smi --query-gpu=compute_cap
  --format=csv`); code built only for older GPUs fails with "no kernel image".
  PyTorch: its CUDA build, not the CPU wheel.
- Rendering on the GPU, where the driver allows: EGL offscreen (`EGL_PLATFORM`
  device or surfaceless), Vulkan (`vulkaninfo --summary`), and `vglrun` for GLX
  apps on the virtual display. Check the renderer string: Mesa's llvmpipe or
  lavapipe means it fell back to the CPU. `agent-gpu-libs` says which of the
  driver's libraries the host mounted (EGL, GLX, Vulkan, NVENC, NVDEC, OptiX).
  On `$DISPLAY` (Xvfb) plain GLX and EGL's X11 platform are llvmpipe: use
  `vglrun`, or EGL's device or surfaceless platform, for the GPU.
- The driver's libraries resolve through `LD_LIBRARY_PATH`, not the `ldconfig`
  cache (Cloud Run mounts them after the image is built), so a process started
  with a cleared environment won't find `libcuda`.
- Video: `ffmpeg`, with NVENC on the GPU (`-c:v h264_nvenc` or `hevc_nvenc`) and
  `libx264` on the CPU.
