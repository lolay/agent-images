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
# Docker Hub's ubuntu through Google's mirror: Docker Hub rate-limits anonymous
# pulls, and mirror.gcr.io caches the same images without that limit. The GPU
# types' parent, base-gpu, is this file on CUDA's devel image instead
# (BASE_IMAGE in docker-bake.hcl), which is built on the same ubuntu:24.04.
ARG BASE_IMAGE=mirror.gcr.io/library/ubuntu:24.04
FROM ${BASE_IMAGE}

RUN apt-get update \
	&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends sudo ca-certificates curl \
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
