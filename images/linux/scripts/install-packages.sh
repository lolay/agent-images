#!/bin/bash
# Installs the toolsets in toolsets.txt (images/toolsets, specs/image-toolsets.md),
# then Claude Code with its native installer (~/.local/bin/claude, which
# `claude update` upgrades; the runner does that at every start). Runs in the
# guest as the guest user, after setup-user.sh. The cloud images run
# install-toolset.sh from their Dockerfiles instead, one layer per toolset.
set -euo pipefail

: "${STAGING_DIR:?}"

log() { printf '==> install-packages: %s\n' "$*"; }

mapfile -t toolsets < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$STAGING_DIR/toolsets.txt")
log "installing toolsets: ${toolsets[*]}"
bash "$STAGING_DIR/toolsets/install-toolset.sh" "${toolsets[@]}"

readonly claude="$HOME/.local/bin/claude"
if [[ ! -x "$claude" ]]; then
	log "installing Claude Code"
	curl -fsSL https://claude.ai/install.sh | bash
fi
log "claude $("$claude" --version)"
