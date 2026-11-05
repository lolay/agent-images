#!/bin/bash
# Installs toolsets (specs/image-toolsets.md) into a Linux image, in the order
# given. For each: its packages.txt from Ubuntu's archive, then its Brewfile
# (installing Homebrew first if it isn't there yet), then its install.sh. A
# toolset has any of the three. Runs as the image's user, who has sudo, from
# the cloud Dockerfiles and the Android image's install-packages.sh.
#
# Usage: install-toolset.sh <name>...
#
# No apt-get upgrade: each build starts from a fresh ubuntu:24.04, the way the
# macOS image doesn't run softwareupdate. needrestart stays out of the build.
set -euo pipefail

toolsets_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly toolsets_dir
readonly brew=/home/linuxbrew/.linuxbrew/bin/brew

log() { printf '==> install-toolset: %s\n' "$*"; }
die() {
	printf '==> install-toolset: %s\n' "$*" >&2
	exit 1
}
apt() { sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get -q "$@"; }

(($# > 0)) || die "usage: install-toolset.sh <name>..."

install_apt() {
	local packages
	mapfile -t packages < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$1")
	((${#packages[@]} > 0)) || return 0
	log "installing ${#packages[@]} apt packages"
	apt update
	apt install -y --no-install-recommends "${packages[@]}"
	apt clean
}

# Homebrew installs to /home/linuxbrew/.linuxbrew and must not run as root; the
# image's user has sudo.
ensure_brew() {
	if [[ ! -x "$brew" ]]; then
		log "installing Homebrew"
		NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
	fi
	brew_env
}

brew_env() {
	eval "$("$brew" shellenv)"
	export HOMEBREW_NO_ENV_HINTS=1 HOMEBREW_NO_ANALYTICS=1
}

install_brewfile() {
	local brewfile="$1" tap formulae
	ensure_brew
	# Homebrew refuses to load formulae from untrusted third-party taps, so trust
	# each tap before the bundle loads them.
	sed -n 's/^tap "\([^"]*\)".*/\1/p' "$brewfile" | while read -r tap; do
		brew tap "$tap"
		brew trust --tap "$tap"
	done
	log "installing $brewfile"
	brew bundle --file="$brewfile"
	# The bottles Homebrew downloaded aren't needed once poured, and on the cloud
	# images each toolset is its own layer, so clean up in it.
	brew cleanup --prune=all -s
	rm -rf "$(brew --cache)"
	mapfile -t formulae < <(sed -n 's/^brew "\([^"]*\)".*/\1/p' "$brewfile")
	log "$(brew list --versions "${formulae[@]}" | tr '\n' ';')"
}

for name in "$@"; do
	dir="$toolsets_dir/$name"
	[[ -d "$dir" ]] || die "no toolset $name in $toolsets_dir"
	log "toolset $name"
	[[ ! -f "$dir/packages.txt" ]] || install_apt "$dir/packages.txt"
	[[ ! -f "$dir/Brewfile" ]] || install_brewfile "$dir/Brewfile"
	if [[ -f "$dir/install.sh" ]]; then
		# A toolset's own steps can use any formula installed so far.
		[[ ! -x "$brew" ]] || brew_env
		bash "$dir/install.sh"
	fi
done
