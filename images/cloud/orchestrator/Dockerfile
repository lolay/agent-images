# agent-orchestrator: runs one Claude orchestrator per runner type on the
# e2-micro host (specs/gcp.md, "The orchestrator container"). Claude Code, curl,
# jq and the scripts; no gcloud. Build it with images/cloud/docker-bake.hcl from
# the repo root. The host runs it as container agent-orchestrator with
# --restart=always and --stop-timeout 85, and passes it nothing: the runner
# types and project settings are baked in from infra/gcp.
# Docker Hub's ubuntu through Google's mirror: Docker Hub rate-limits anonymous
# pulls, and mirror.gcr.io caches the same images without that limit.
FROM mirror.gcr.io/library/ubuntu:24.04

# apt-build.conf: a part of the archive that fails to download fails the build.
RUN --mount=type=bind,source=images/cloud/base/apt-build.conf,target=/tmp/apt-build.conf \
	APT_CONFIG=/tmp/apt-build.conf apt-get update \
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
COPY hooks/gcp/ /opt/agent-orchestrator/hooks/gcp/
COPY infra/gcp/ /opt/agent-orchestrator/infra/gcp/

ENTRYPOINT ["/opt/agent-orchestrator/supervisor.sh"]
