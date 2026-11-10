# agent-<type>: the last stage of every Cloud Run runner type (specs/gcp.md,
# "Images"), on top of that type's toolsets (agent-tools: images/cloud/<type>,
# built FROM agent-base). The runner (images/shared), the session's Claude Code
# settings and CLAUDE.md, the login environment and the entrypoint, then Claude
# Code itself last, since it moves most often: a Claude release rebuilds only
# that layer, never a type's toolsets. Build it with
# images/cloud/docker-bake.hcl, which supplies agent-tools per type, and the
# type and commit for the versions report, the last layer; the versions stage
# is that report alone (bake target versions-<type>).
FROM agent-tools AS runner

# The runner (images/shared), the Claude Code settings the runner seeds into
# every session (the shared hooks plus the cloud SessionStart hook), the
# cloud CLAUDE.md, the login environment, the entrypoint and the runner of the
# toolsets' binary checks and smoke tests (on PATH, so sessions can run them too).
RUN --mount=type=bind,source=images/shared,target=/tmp/images/shared \
	--mount=type=bind,source=images/cloud/runner,target=/tmp/images/runner \
	--mount=type=bind,source=images/cloud/entrypoint.sh,target=/tmp/images/entrypoint.sh \
	--mount=type=bind,source=images/toolsets/write-claude-md.sh,target=/tmp/images/write-claude-md.sh \
	--mount=type=bind,source=images/toolsets/toolset-checks.sh,target=/tmp/images/toolset-checks.sh \
	--mount=type=bind,source=images/toolsets/skills,target=/tmp/images/skills \
	<<'SCRIPT'
set -eu
shared=/tmp/images/shared
runner=/tmp/images/runner
install -d -m 755 "$HOME/bin" "$HOME/.claude/hooks" "$HOME/.config/agent-runner" "$HOME/.local/bin"
install -d -m 700 "$HOME/.claude-runner"
install -m 755 "$shared/agent-runner.sh" "$HOME/bin/agent-runner"
install -m 755 "$shared/agent-runner-watchdog.sh" "$HOME/bin/agent-runner-watchdog"
for script in "$shared"/runners/*.sh; do
	install -m 755 "$script" "$HOME/bin/agent-runner-$(basename "$script" .sh)"
done
for hook in "$shared"/claude/hooks/*.sh "$runner"/claude/hooks/*.sh; do
	[ -e "$hook" ] || continue
	install -m 755 "$hook" "$HOME/.claude/hooks/$(basename "$hook")"
done
bash "$runner/merge-settings.sh" "$shared/claude/settings.json" "$runner/claude/settings.json" >"$HOME/.claude/settings.json"
# The cloud CLAUDE.md, with a line per toolset the type installed.
bash /tmp/images/write-claude-md.sh "$runner/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
sudo install -m 644 "$runner/profile.sh" /etc/profile.d/agent-images.sh
sudo install -D -m 644 "$shared/profile-gh-shim.sh" /etc/agent-images/profile-gh-shim.sh
# Interactive shells that aren't login shells read only ~/.bashrc, so it sources
# the profile too, above Ubuntu's early return for non-interactive shells.
{ printf '# agent-images: PATH, scratch and caches (images/cloud/runner/profile.sh)\n. /etc/profile.d/agent-images.sh\n\n'; cat "$HOME/.bashrc"; } >"$HOME/.bashrc.new"
mv "$HOME/.bashrc.new" "$HOME/.bashrc"
sudo install -m 755 /tmp/images/entrypoint.sh /usr/local/bin/agent-cloud-entrypoint
sudo install -m 755 /tmp/images/toolset-checks.sh /usr/local/bin/agent-toolset-checks
# The session skills that drive it (/agent-toolset-checks); the runner seeds
# ~/.claude/skills into every session.
install -d -m 755 "$HOME/.claude/skills"
cp -R /tmp/images/skills/. "$HOME/.claude/skills/"
# Mount points: Cloud Run mounts over them; a local docker volume (make
# cloud-smoke) takes their owner, so the session user can write scratch and outputs.
sudo install -d -m 755 -o ubuntu -g ubuntu /scratch /mnt/outputs
sudo install -d -m 755 /mnt/datasets
# The toolsets are in; a session's own apt (on a runtime that allows sudo) reads
# today's archive, not the build's snapshot (images/cloud/base/Dockerfile).
sudo rm -f /etc/apt/apt.conf.d/50agent-snapshot
SCRIPT

# profile.sh's PATH: Homebrew (and rustup's proxies) ahead of the system.
# Non-interactive bash (`bash -c`, scripts) reads neither profile nor bashrc,
# only BASH_ENV.
ENV BASH_ENV=/etc/profile.d/agent-images.sh
ENV PATH=/home/ubuntu/bin:/home/ubuntu/.local/bin:/home/linuxbrew/.linuxbrew/opt/rustup/bin:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Claude Code with its native installer (~/.local/bin/claude): an exact version,
# or a channel.
ARG CLAUDE_VERSION=latest
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_VERSION" \
	&& claude --version

# What the image contains (specs/gcp.md, m4 "Versions report"). Last, so it
# reruns whenever any layer under it changed, and a cached copy is still true.
ARG AGENT_TYPE=unknown
ARG AGENT_COMMIT=unknown
RUN --mount=type=bind,source=images/toolsets/write-versions.sh,target=/tmp/images/write-versions.sh \
	bash -o pipefail -c 'bash /tmp/images/write-versions.sh "$AGENT_TYPE" "$AGENT_COMMIT" | sudo tee /etc/agent-images/versions.md >/dev/null'

ENTRYPOINT ["/usr/local/bin/agent-cloud-entrypoint"]

# The report alone, for the workflows' summaries: built with the image, so it
# needs no pull from the registry.
FROM scratch AS versions
COPY --from=runner /etc/agent-images/versions.md /versions.md
