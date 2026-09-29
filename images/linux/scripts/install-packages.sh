#!/bin/bash
# Installs packages.txt from Ubuntu's archive, then Claude Code with its native
# installer as the agent user (~/.local/bin/claude, which `claude update`
# upgrades; the runner does that at every start). Runs in the guest as root,
# after create-user.sh.
set -euo pipefail

: "${STAGING_DIR:?}" "${AGENT_USER:?}"

log() { printf '==> install-packages: %s\n' "$*"; }

export DEBIAN_FRONTEND=noninteractive

mapfile -t packages < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$STAGING_DIR/packages.txt")
log "installing ${#packages[@]} packages"
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q --no-install-recommends "${packages[@]}"
apt-get autoremove -y -q
apt-get clean

if ! sudo -u "$AGENT_USER" -H bash -lc 'command -v claude' >/dev/null 2>&1; then
	log "installing Claude Code for $AGENT_USER"
	sudo -u "$AGENT_USER" -H bash -lc 'curl -fsSL https://claude.ai/install.sh | bash'
fi
log "claude $(sudo -u "$AGENT_USER" -H bash -lc 'claude --version')"
