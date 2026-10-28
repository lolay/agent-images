#!/bin/bash
# Installs packages.txt from Ubuntu's archive, nowline from its latest GitHub
# release (the binary lolay/tap's formula ships), then Claude Code with its
# native installer (~/.local/bin/claude, which `claude update` upgrades; the
# runner does that at every start). Runs in the guest as the guest user, after setup-user.sh.
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

# The image is x86_64 only (the emulator needs it), so the linux-x64 asset.
readonly nowline_release=https://github.com/lolay/nowline/releases/latest/download
log "installing nowline"
sudo curl -fsSL -o /usr/local/bin/nowline "$nowline_release/nowline-linux-x64"
sudo chmod 755 /usr/local/bin/nowline
for page in nowline.1 nowline.5; do
	sudo install -d /usr/local/share/man/man"${page##*.}"
	sudo curl -fsSL -o "/usr/local/share/man/man${page##*.}/$page" "$nowline_release/$page"
done
log "nowline $(nowline --version)"

readonly claude="$HOME/.local/bin/claude"
if [[ ! -x "$claude" ]]; then
	log "installing Claude Code"
	curl -fsSL https://claude.ai/install.sh | bash
fi
log "claude $("$claude" --version)"
