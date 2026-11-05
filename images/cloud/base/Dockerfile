# agent-base: what every Cloud Run runner type is built FROM (specs/gcp.md,
# "Images"). Ubuntu 24.04, the toolsets, the shared runner files, and Claude
# Code last, since it moves most often. Build it with
# images/cloud/docker-bake.hcl from the repo root.
#
# The user is the stock image's ubuntu (uid 1000) with passwordless sudo, like
# the Linux image's ubuntu and the macOS image's admin: sessions install what a
# repo needs, and nothing outlives the execution.
FROM ubuntu:24.04

RUN apt-get update \
	&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends sudo ca-certificates curl \
	&& rm -rf /var/lib/apt/lists/* \
	&& printf 'ubuntu ALL=(ALL) NOPASSWD:ALL\n' >/etc/sudoers.d/ubuntu \
	&& chmod 440 /etc/sudoers.d/ubuntu

USER ubuntu
WORKDIR /home/ubuntu

# The toolsets (images/toolsets, specs/image-toolsets.md), one layer each. Each
# layer mounts only the installer and its own toolset, so a change to one
# toolset rebuilds its layer and those after it, not the ones before. The same
# list as the Android image's images/linux/toolsets.txt for now; it narrows to
# what this image needs in s3. apt toolsets first: they move slowest.
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/core,target=/tmp/toolsets/core \
	bash /tmp/toolsets/install-toolset.sh core
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/jvm,target=/tmp/toolsets/jvm \
	bash /tmp/toolsets/install-toolset.sh jvm
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/browser,target=/tmp/toolsets/browser \
	bash /tmp/toolsets/install-toolset.sh browser
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/android,target=/tmp/toolsets/android \
	bash /tmp/toolsets/install-toolset.sh android
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/node,target=/tmp/toolsets/node \
	bash /tmp/toolsets/install-toolset.sh node
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/python,target=/tmp/toolsets/python \
	bash /tmp/toolsets/install-toolset.sh python
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/go,target=/tmp/toolsets/go \
	bash /tmp/toolsets/install-toolset.sh go
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/rust,target=/tmp/toolsets/rust \
	bash /tmp/toolsets/install-toolset.sh rust
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/proto,target=/tmp/toolsets/proto \
	bash /tmp/toolsets/install-toolset.sh proto
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/terraform,target=/tmp/toolsets/terraform \
	bash /tmp/toolsets/install-toolset.sh terraform

# The runner (images/shared), the Claude Code settings the runner seeds into
# every session (the shared Stop hook plus the cloud SessionStart hook), the
# cloud CLAUDE.md, the login environment and the entrypoint.
RUN --mount=type=bind,source=images,target=/tmp/images <<'SCRIPT'
set -eu
shared=/tmp/images/shared
cloud=/tmp/images/cloud
install -d -m 755 "$HOME/bin" "$HOME/.claude/hooks" "$HOME/.config/agent-runner" "$HOME/.local/bin"
install -d -m 700 "$HOME/.claude-runner"
install -m 755 "$shared/agent-runner.sh" "$HOME/bin/agent-runner"
install -m 755 "$shared/agent-runner-watchdog.sh" "$HOME/bin/agent-runner-watchdog"
for runner in "$shared"/runners/*.sh; do
	install -m 755 "$runner" "$HOME/bin/agent-runner-$(basename "$runner" .sh)"
done
for hook in "$shared"/claude/hooks/*.sh "$cloud"/base/claude/hooks/*.sh; do
	[ -e "$hook" ] || continue
	install -m 755 "$hook" "$HOME/.claude/hooks/$(basename "$hook")"
done
jq -s '.[0] * .[1]' "$shared/claude/settings.json" "$cloud/base/claude/settings.json" >"$HOME/.claude/settings.json"
install -m 644 "$cloud/base/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
sudo install -m 644 "$cloud/base/profile.sh" /etc/profile.d/agent-images.sh
sudo install -m 755 "$cloud/entrypoint.sh" /usr/local/bin/agent-cloud-entrypoint
# Mount points: Cloud Run mounts over them; a local docker volume (make
# cloud-smoke) takes their owner, so the session user can write scratch and outputs.
sudo install -d -m 755 -o ubuntu -g ubuntu /scratch /mnt/outputs
sudo install -d -m 755 /mnt/datasets
SCRIPT

# profile.sh's PATH: Homebrew (and rustup's proxies) ahead of the system.
ENV PATH=/home/ubuntu/bin:/home/ubuntu/.local/bin:/home/linuxbrew/.linuxbrew/opt/rustup/bin:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Claude Code with its native installer (~/.local/bin/claude): an exact version,
# or a channel.
ARG CLAUDE_VERSION=latest
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_VERSION" \
	&& claude --version

ENTRYPOINT ["/usr/local/bin/agent-cloud-entrypoint"]
