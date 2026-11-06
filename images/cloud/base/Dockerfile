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
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# packages.txt: what the base needs before any toolset (sudo, and curl for
# Homebrew's installer); the lock resolves it with the toolsets' lists.
RUN --mount=type=bind,source=images/cloud/base/packages.txt,target=/tmp/base-packages.txt \
	apt-get update \
	&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
		$(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' /tmp/base-packages.txt) \
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
