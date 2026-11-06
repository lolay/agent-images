#!/bin/bash
# agent-gpu-libs: which of the NVIDIA driver's libraries the host mounted (Cloud
# Run: /usr/local/nvidia/lib64), grouped by what each makes possible. The gpu
# toolset's smoke check runs it, report-only, so every deploy of a GPU type logs
# whether rendering (EGL, GLX, Vulkan), video (NVENC, NVDEC) and OptiX can reach
# the GPU at all (specs/gcp.md, "The head"). Prints one line; always exits 0.
#
#   AGENT_NVIDIA_LIB   the directory to look in (default /usr/local/nvidia/lib64)
set -euo pipefail

readonly dir="${AGENT_NVIDIA_LIB:-/usr/local/nvidia/lib64}"

if [[ ! -d "$dir" ]]; then
	printf 'no driver mounted at %s\n' "$dir"
	exit 0
fi

# What each group needs, by library name (any version suffix).
readonly groups=(
	"compute:libcuda.so libnvidia-ml.so libnvidia-ptxjitcompiler.so"
	"egl:libEGL_nvidia.so libnvidia-eglcore.so"
	"glx:libGLX_nvidia.so libnvidia-glcore.so libnvidia-glsi.so"
	"vulkan:libGLX_nvidia.so libnvidia-glvkspirv.so"
	"nvenc:libnvidia-encode.so"
	"nvdec:libnvcuvid.so"
	"optix:libnvoptix.so libnvidia-rtcore.so"
)

has() { compgen -G "$dir/$1*" >/dev/null; }

version="$(compgen -G "$dir/libcuda.so.*.*" | head -n 1 || true)"
version="${version##*/libcuda.so.}"

present=() missing=()
for group in "${groups[@]}"; do
	name="${group%%:*}" ok=true
	absent=()
	for lib in ${group#*:}; do
		has "$lib" || {
			ok=false
			absent+=("$lib")
		}
	done
	if $ok; then
		present+=("$name")
	else
		missing+=("$name (${absent[*]})")
	fi
done

missing_text=""
for group in "${missing[@]}"; do
	missing_text+="${missing_text:+, }$group"
done
printf 'driver %s; mounted: %s; missing: %s\n' "${version:-unknown}" \
	"${present[*]:-none}" "${missing_text:-none}"
