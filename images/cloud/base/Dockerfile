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

# apt reads Ubuntu's archive as it was at APT_SNAPSHOT, the type's locked
# snapshot (specs/gcp.md, "The lock"), in this layer and every toolset's after
# it; the runner stage drops the setting at the end. snapshot.ubuntu.com is
# HTTPS only and the stock image has no CA certificates, so ca-certificates
# (and on plain Ubuntu its openssl) comes from today's archive first: the one
# apt install the snapshot doesn't hold. packages.txt is what the base needs
# before any toolset (sudo, and curl for Homebrew's installer).
ARG APT_SNAPSHOT
RUN --mount=type=bind,source=images/cloud/base/packages.txt,target=/tmp/base-packages.txt \
	: "${APT_SNAPSHOT:?the build passes the lock's apt-snapshot (scripts/cloud/lock.sh bake-args)}" \
	&& apt-get update \
	&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates \
	&& printf 'APT::Snapshot "%s";\n' "$APT_SNAPSHOT" >/etc/apt/apt.conf.d/50agent-snapshot \
	&& apt-get update \
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
