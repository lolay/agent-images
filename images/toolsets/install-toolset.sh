#!/bin/bash
# Installs toolsets (specs/image-toolsets.md) into a Linux image, in the order
# given. For each: its packages.txt from Ubuntu's archive, then its Brewfile
# (installing Homebrew first if it isn't there yet), then its install.sh. A
# toolset has any of the three, and optionally:
#   CLAUDE.md    its lines for the session's CLAUDE.md (write-claude-md.sh);
#   check.txt    its smoke checks, `label: command` (images/cloud/entrypoint.sh);
#   profile.sh   environment for every session shell, sourced by the cloud
#                profile after its own;
#   path.txt     directories for the front of every session's PATH, after ~/bin
#                and before Homebrew's.
# Each installed toolset is recorded in record_dir with those files. install.sh
# gets TOOLSET_RECORD_DIR, and writes generated ones (<name>.profile.sh) there. Runs as the image's user, who has sudo, from
# the cloud Dockerfiles and the Android image's install-packages.sh.
#
# Usage: install-toolset.sh <name>...
#
# APT_DROP_LISTS=true deletes apt's package lists after each toolset's install.
# The cloud Dockerfiles set it: every toolset is its own layer, so each kept
# copy of the lists (about 50 MB) would stay in the image, and sessions there
# can't run apt anyway. The VM images keep them, for sessions' own apt installs.
#
# No apt-get upgrade: each build starts from a fresh ubuntu:24.04, the way the
# macOS image doesn't run softwareupdate. needrestart stays out of the build.
set -euo pipefail

toolsets_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly toolsets_dir
readonly brew=/home/linuxbrew/.linuxbrew/bin/brew
readonly record_dir=/etc/agent-images/toolsets

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
	if [[ "${APT_DROP_LISTS:-false}" == "true" ]]; then
		sudo rm -rf /var/lib/apt/lists/*
	fi
}

# Records an installed toolset: its name once in toolsets.txt, in install order,
# and its CLAUDE.md, check.txt, profile.sh and path.txt as <name>.md,
# <name>.check, <name>.profile.sh and <name>.path.
record() {
	local name="$1" dir="$2" file suffix
	sudo install -d -m 755 "$record_dir"
	sudo touch "$record_dir/toolsets.txt"
	grep -qxF "$name" "$record_dir/toolsets.txt" || printf '%s\n' "$name" | sudo tee -a "$record_dir/toolsets.txt" >/dev/null
	for file in CLAUDE.md:md check.txt:check profile.sh:profile.sh path.txt:path; do
		suffix="${file#*:}" file="${file%%:*}"
		[[ ! -f "$dir/$file" ]] || sudo install -m 644 "$dir/$file" "$record_dir/$name.$suffix"
	done
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
	# brew update and tap report routine progress on stderr (downloading the API
	# data, tapping), which a green build doesn't allow, so they go to stdout, as
	# in the macOS image. The update also refreshes the API data, which the last
	# toolset's cleanup deleted, so brew bundle doesn't fetch it on stderr.
	# brew bundle keeps stderr, so a real error still stands out.
	brew update --quiet 2>&1
	# Homebrew refuses to load formulae from untrusted third-party taps, so trust
	# each tap before the bundle loads them.
	sed -n 's/^tap "\([^"]*\)".*/\1/p' "$brewfile" | while read -r tap; do
		brew tap "$tap" 2>&1
		brew trust --tap "$tap"
	done
	log "installing $brewfile"
	brew bundle --file="$brewfile"
	# brew bundle list evaluates the Brewfile, so it skips entries for other
	# platforms (brew "git" if OS.mac?).
	mapfile -t formulae < <(brew bundle list --formula --file="$brewfile")
	log "$(brew list --versions "${formulae[@]}" | tr '\n' ';')"
	# The bottles Homebrew downloaded and its API and bootsnap caches aren't
	# needed once poured. On the cloud images each toolset is its own layer, so
	# clean up last, in the same layer; any brew command after this refills the
	# cache.
	brew cleanup --prune=all -s
	rm -rf "$(brew --cache)"
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
		sudo install -d -m 755 "$record_dir"
		TOOLSET_RECORD_DIR="$record_dir" bash "$dir/install.sh"
		# Any brew command it ran refilled Homebrew's cache.
		[[ ! -x "$brew" ]] || rm -rf "$("$brew" --cache)"
	fi
	record "$name" "$dir"
done
