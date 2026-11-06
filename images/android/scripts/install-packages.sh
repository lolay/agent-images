#!/bin/bash
# Installs the toolsets in toolsets.txt (images/toolsets, specs/image-toolsets.md),
# then Claude Code with its native installer (~/.local/bin/claude, which
# `claude update` upgrades; the runner does that at every start). Runs in the
# guest as the guest user, after setup-user.sh. The cloud images run
# install-toolset.sh from their Dockerfiles instead, one layer per toolset.
#
# Last, the binary checks' runner (agent-toolset-checks), the session skills that
# drive it and the versions report (/etc/agent-images/versions.md; specs/gcp.md,
# m4), so every android image, whichever driver built it (Packer, the LXD build,
# android-image-smoke.yml), has them, and the report lists what is installed.
# Needs sudo (passwordless for the guest user, as setup-user.sh checks).
#
#   AGENT_COMMIT   the commit being built, for the report (default unknown)
set -euo pipefail

: "${STAGING_DIR:?}"

log() { printf '==> install-packages: %s\n' "$*"; }

mapfile -t toolsets < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$STAGING_DIR/toolsets.txt")
log "installing toolsets: ${toolsets[*]}"
bash "$STAGING_DIR/toolsets/install-toolset.sh" "${toolsets[@]}"
# setup-user.sh installed CLAUDE.md as the template; fill in the toolsets.
bash "$STAGING_DIR/toolsets/write-claude-md.sh" "$STAGING_DIR/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"

readonly claude="$HOME/.local/bin/claude"
if [[ ! -x "$claude" ]]; then
	log "installing Claude Code"
	curl -fsSL https://claude.ai/install.sh | bash
fi
log "claude $("$claude" --version)"

# On PATH, so sessions can run a toolset's checks too, as on the Cloud Run images.
sudo install -m 755 "$STAGING_DIR/toolsets/toolset-checks.sh" /usr/local/bin/agent-toolset-checks
# The session skills that drive it (/agent-toolset-checks), beside android init's
# android-cli; the runner seeds ~/.claude/skills into every session.
mkdir -p "$HOME/.claude/skills"
cp -R "$STAGING_DIR/toolsets/skills/." "$HOME/.claude/skills/"

# The report, after everything above is installed. Through a login shell: its
# PATH finds Homebrew, rustup and the checks a session would. It goes to the
# provisioner's stdout (the build log) too.
log "versions report (/etc/agent-images/versions.md)"
sudo install -d -m 755 /etc/agent-images
bash -lc 'exec bash "$0" android "$1"' "$STAGING_DIR/toolsets/write-versions.sh" "${AGENT_COMMIT:-unknown}" |
	sudo tee /etc/agent-images/versions.md
