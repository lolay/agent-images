#!/bin/bash
# Installs packages.txt from Ubuntu's archive, then Claude Code with its native
# installer (~/.local/bin/claude, which `claude update` upgrades; the runner does
# that at every start). Runs in the guest as the guest user, after setup-user.sh.
#
# No apt-get upgrade: each build starts from a fresh ubuntu:24.04, the way the
# macOS image doesn't run softwareupdate. needrestart stays out of the build.
set -euo pipefail

: "${STAGING_DIR:?}"

log() { printf '==> install-packages: %s\n' "$*"; }
apt() { sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get -q "$@"; }

mapfile -t packages < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$STAGING_DIR/packages.txt")
log "installing ${#packages[@]} packages"
apt update
apt install -y --no-install-recommends "${packages[@]}"
apt clean

readonly claude="$HOME/.local/bin/claude"
if [[ ! -x "$claude" ]]; then
	log "installing Claude Code"
	curl -fsSL https://claude.ai/install.sh | bash
fi
log "claude $("$claude" --version)"
