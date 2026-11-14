# agent-3d-gpu's toolsets (specs/gcp.md, "Runner types"; specs/image-toolsets.md,
# "3d"): 3D reconstruction on the GPU, on agent-base-gpu (CUDA's devel image):
# core, native, display, 3d, then gpu last as in linux-gpu. No Go, Rust,
# Terraform, JDK or Chromium libraries: a session that needs one installs it.
# images/cloud/runner then adds the runner files and Claude Code. Build it with
# images/cloud/docker-bake.hcl, which supplies agent-base (base-gpu).

# COLMAP from source with CUDA (images/toolsets/3d/build-colmap.sh). Only its
# install tree reaches the image, with the list of Ubuntu packages its binary
# links, which the 3d toolset installs. COLMAP_VERSION is the lock's.
FROM agent-base AS colmap
ARG COLMAP_VERSION
RUN --mount=type=bind,source=images/toolsets/3d/build-colmap.sh,target=/tmp/build-colmap.sh \
	bash /tmp/build-colmap.sh

FROM agent-base

ARG APT_DROP_LISTS=true
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/native,target=/tmp/toolsets/native \
	bash /tmp/toolsets/install-toolset.sh native
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/display,target=/tmp/toolsets/display \
	bash /tmp/toolsets/install-toolset.sh display
COPY --from=colmap /opt/colmap /opt/colmap
# The locked Blender release (scripts/cloud/lock.sh bake-args).
ARG BLENDER_VERSION
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/3d,target=/tmp/toolsets/3d \
	bash /tmp/toolsets/install-toolset.sh 3d

# As in linux-gpu: ask the container runtime for every driver capability, so the
# graphics libraries (EGL, GLX, Vulkan) and NVENC are mounted with compute.
ENV NVIDIA_DRIVER_CAPABILITIES=all

# The locked VirtualGL release (scripts/cloud/lock.sh bake-args).
ARG VIRTUALGL_VERSION
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/gpu,target=/tmp/toolsets/gpu \
	bash /tmp/toolsets/install-toolset.sh gpu
