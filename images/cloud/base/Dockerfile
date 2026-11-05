# agent-base: what every Cloud Run runner type is built FROM (specs/gcp.md,
# "Images"). Ubuntu 24.04, the Linux image's apt packages and Brewfile, the
# shared runner files, and Claude Code last, since it moves most often. Build it
# with images/cloud/docker-bake.hcl from the repo root.
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

# apt packages, Homebrew and the Brewfile, with the Linux image's own script.
RUN --mount=type=bind,source=images/linux,target=/tmp/agent-images/linux <<'SCRIPT'
set -eu
mkdir -p /tmp/staging/scripts
cp /tmp/agent-images/linux/packages.txt /tmp/agent-images/linux/Brewfile /tmp/staging/
cp /tmp/agent-images/linux/scripts/install-packages.sh /tmp/staging/scripts/
STAGING_DIR=/tmp/staging INSTALL_CLAUDE=false bash /tmp/staging/scripts/install-packages.sh
rm -rf /tmp/staging
SCRIPT

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
sudo install -d -m 755 /scratch /mnt/outputs /mnt/datasets
SCRIPT

ENV PATH=/home/ubuntu/bin:/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin

# Claude Code with its native installer (~/.local/bin/claude): an exact version,
# or a channel.
ARG CLAUDE_VERSION=latest
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_VERSION" \
	&& claude --version

ENTRYPOINT ["/usr/local/bin/agent-cloud-entrypoint"]
