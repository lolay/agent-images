# agent-base: what every Cloud Run runner type's toolsets are built FROM
# (specs/gcp.md, "Images"; specs/image-toolsets.md): Ubuntu 24.04, the user, and
# the core toolset every type has. Each type's Dockerfile (images/cloud/<type>)
# adds its toolsets, then images/cloud/runner adds the runner files and Claude
# Code last. A build parent only, never deployed. Build it with
# images/cloud/docker-bake.hcl from the repo root.
#
# The user is the stock image's ubuntu (uid 1000) with passwordless sudo, like
# the Linux image's ubuntu and the macOS image's admin: sessions install what a
# repo needs, and nothing outlives the execution.
# BASE_IMAGE comes from docker-bake.hcl, the one place it's declared: Ubuntu
# through Google's mirror for base, CUDA's devel image (on the same Ubuntu) for
# base-gpu. The build passes the lock's digest for it (scripts/cloud/lock.sh).
# base is the stage of that name; base-gpu is the gpu stage at the end, on CUDA.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS base

# apt reads Ubuntu's archive as it was at APT_SNAPSHOT, the type's locked
# snapshot (specs/gcp.md, "The lock"), in this layer and every toolset's after
# it; the runner stage drops the setting at the end. apt-prepare.sh is the
# sequence that gets it there, the one the lock's resolve simulates: the build's
# apt settings (apt-build.conf, so a part of the archive that fails to download
# fails this layer and every toolset's rather than leaving its packages out),
# ca-certificates from today's archive (snapshot.ubuntu.com is HTTPS only), the
# snapshot, the pins, then packages.txt, what the base needs before any toolset
# (sudo, and curl for Homebrew's installer).
# APT_PINS is the pins that name their own snapshot, which lock.sh bake-args
# reads from the type's package lists: apt-pins.sh adds each snapshot as a
# source and makes the pinned version the candidate for that package alone,
# here and in every toolset layer (the runner stage removes them too). It
# refuses a pin's snapshot newer than APT_SNAPSHOT.
ARG APT_SNAPSHOT
ARG APT_PINS
RUN --mount=type=bind,source=images/cloud/base/packages.txt,target=/tmp/base-packages.txt \
	--mount=type=bind,source=images/cloud/base/apt-prepare.sh,target=/tmp/apt-prepare.sh \
	--mount=type=bind,source=images/cloud/base/apt-build.conf,target=/tmp/apt-build.conf \
	--mount=type=bind,source=images/cloud/base/apt-pins.sh,target=/tmp/apt-pins.sh \
	--mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/install-toolset.sh \
	bash /tmp/apt-prepare.sh --list /tmp/base-packages.txt \
	&& rm -rf /var/lib/apt/lists/* \
	&& printf 'ubuntu ALL=(ALL) NOPASSWD:ALL\n' >/etc/sudoers.d/ubuntu \
	&& chmod 440 /etc/sudoers.d/ubuntu

USER ubuntu
WORKDIR /home/ubuntu

# Toolsets (images/toolsets) are one layer each, and each layer mounts only the
# installer and its own toolset, so a change to one toolset rebuilds its layer
# and those after it, not the ones before. APT_DROP_LISTS: see install-toolset.sh.
ARG APT_DROP_LISTS=true
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/core,target=/tmp/toolsets/core \
	bash /tmp/toolsets/install-toolset.sh core

# agent-base-gpu (bake target base-gpu): what every GPU type needs beyond CUDA's
# image, set once here rather than in each GPU type's Dockerfile.
# - NVIDIA_DRIVER_CAPABILITIES=all: CUDA's image asks the container runtime for
#   compute and utility only, which under NVIDIA's container toolkit leaves the
#   graphics libraries (EGL, GLX, Vulkan), NVENC and OptiX out of the driver
#   mount the gpu toolset routes to. Cloud Run documents mounting all of them
#   anyway, so at worst this changes nothing.
# - CUDAARCHS (CMake) and TORCH_CUDA_ARCH_LIST (PyTorch): the GPUs every CUDA
#   build here targets, from docker-bake.hcl's CUDA_CAPABILITIES, the one place
#   that list lives. The toolsets' builds read them (and die without them), and
#   so do a session's.
FROM base AS gpu
ARG CUDAARCHS
ARG TORCH_CUDA_ARCH_LIST
ENV NVIDIA_DRIVER_CAPABILITIES=all \
	CUDAARCHS=${CUDAARCHS} \
	TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}
