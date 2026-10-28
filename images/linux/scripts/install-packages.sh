#!/bin/bash
# Installs packages.txt from Ubuntu's archive, Homebrew and the Brewfile (the
# lolay/tap formulae), then Claude Code with its native installer
# (~/.local/bin/claude, which `claude update` upgrades; the runner does that at
# every start). Runs in the guest as the guest user, after setup-user.sh.
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

# Homebrew, for the lolay/tap formulae (the Brewfile). It installs to
# /home/linuxbrew/.linuxbrew and must not run as root; the guest user has sudo.
readonly brewfile="$STAGING_DIR/Brewfile"
readonly brew=/home/linuxbrew/.linuxbrew/bin/brew
if [[ ! -x "$brew" ]]; then
	log "installing Homebrew"
	NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
eval "$("$brew" shellenv)"
export HOMEBREW_NO_ENV_HINTS=1 HOMEBREW_NO_ANALYTICS=1

# Homebrew refuses to load formulae from untrusted third-party taps, so trust
# each Brewfile tap before the bundle loads them.
sed -n 's/^tap "\([^"]*\)".*/\1/p' "$brewfile" | while read -r tap; do
	brew tap "$tap"
	brew trust --tap "$tap"
done
log "installing the Brewfile"
brew bundle --file="$brewfile"
log "$(brew list --versions nowline triage | tr '\n' ';')"

readonly claude="$HOME/.local/bin/claude"
if [[ ! -x "$claude" ]]; then
	log "installing Claude Code"
	curl -fsSL https://claude.ai/install.sh | bash
fi
log "claude $("$claude" --version)"
