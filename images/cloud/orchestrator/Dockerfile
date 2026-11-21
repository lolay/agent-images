# agent-orchestrator: runs one Claude orchestrator per runner type on the
# e2-micro host (specs/gcp.md, "The orchestrator container"). Claude Code, curl,
# jq and the scripts; no gcloud. Build it with images/cloud/docker-bake.hcl from
# the repo root. The host runs it as container agent-orchestrator with
# --restart=always and --stop-timeout 85, and passes it nothing: the runner
# types and project settings are baked in from infra/gcp.
# Docker Hub's ubuntu through Google's mirror: Docker Hub rate-limits anonymous
# pulls, and mirror.gcr.io caches the same images without that limit.
# Why 24.04: images/cloud/docker-bake.hcl, beside the base target's BASE_IMAGE.
FROM mirror.gcr.io/library/ubuntu:24.04

# apt-build.conf: a part of the archive that fails to download fails the build.
# apt_update is install-toolset.sh's, which every cloud build's apt update goes
# through: it runs again while the archive is out of sync.
RUN --mount=type=bind,source=images/cloud/base/apt-build.conf,target=/tmp/apt-build.conf \
	--mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/install-toolset.sh \
	export APT_CONFIG=/tmp/apt-build.conf \
	&& bash -c 'source /tmp/install-toolset.sh && apt_update' \
	&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl jq procps \
	&& rm -rf /var/lib/apt/lists/*

USER ubuntu
WORKDIR /home/ubuntu
ENV PATH=/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
	AGENT_ORCHESTRATOR_ROOT=/opt/agent-orchestrator

# Claude Code first here: the scripts and env files change more often than it.
ARG CLAUDE_VERSION=latest
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_VERSION" \
	&& claude --version

# The repo's layout, so hooks/gcp/lib.sh finds infra/gcp the same way here.
COPY --chmod=755 images/cloud/orchestrator/supervisor.sh /opt/agent-orchestrator/supervisor.sh
COPY images/cloud/orchestrator/orchestrator-args.sh /opt/agent-orchestrator/orchestrator-args.sh
COPY hooks/gcp/ /opt/agent-orchestrator/hooks/gcp/
COPY infra/gcp/ /opt/agent-orchestrator/infra/gcp/

ENTRYPOINT ["/opt/agent-orchestrator/supervisor.sh"]
