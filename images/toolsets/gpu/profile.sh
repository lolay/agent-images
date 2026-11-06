# shellcheck shell=sh
# The gpu toolset (images/toolsets/gpu): CUDA's toolkit for builds, the driver
# Cloud Run mounts for everything that loads it, and VirtualGL's EGL back end,
# since there's no GPU X server for it to use.
export CUDA_HOME=/usr/local/cuda
case ":${LD_LIBRARY_PATH:-}:" in
*:/usr/local/nvidia/lib64:*) ;;
*) export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}/usr/local/nvidia/lib64" ;;
esac
export VGL_DISPLAY="${VGL_DISPLAY:-egl}"
