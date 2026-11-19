#!/bin/bash
# Installs toolsets (specs/image-toolsets.md) into a Linux image, in the order
# given. For each: its packages.txt from Ubuntu's archive, then its Brewfile
# (installing Homebrew first if it isn't there yet), then its install.sh. A
# toolset has any of the three, and optionally:
#   CLAUDE.md    its lines for the session's CLAUDE.md (write-claude-md.sh);
#   check.txt    its binary checks, `label: command` (toolset-checks.sh);
#   smoke.sh     its smoke test, the least real work that shows the toolchain
#                works (toolset-checks.sh; specs/gcp.md, m4);
#   profile.sh   environment for every session shell, sourced by the cloud
#                profile after its own;
#   path.txt     directories for the front of every session's PATH, after ~/bin
#                and before Homebrew's.
# Each installed toolset is recorded in record_dir with those files. install.sh
# gets TOOLSET_RECORD_DIR, and writes generated ones (<name>.profile.sh) there.
# Runs as the image's user, who has sudo, from the cloud Dockerfiles and the
# Android image's install-packages.sh.
#
# Usage: install-toolset.sh <name>...
#
# Its helpers are the toolsets' too, so no toolset script restates them: log,
# die, apt (apt-get as root, no prompts), apt_update (the one `apt-get update`
# of the cloud builds, retried while the archive is out of sync),
# apt_install_options (what every apt
# install of a package list takes; images/cloud/base/apt-prepare.sh sources this
# file for it, so the base's install and the lock's simulation take them too),
# install_apt <file> (a packages.txt), install_apt_packages <package|.deb>...
# and github_latest <releases URL> (the newest release's tag;
# scripts/cloud/lock.sh sources this file for it too, so the build and the lock
# read a release the same way). A toolset's install.sh
# gets them exported, with TOOLSET_LOG_NAME, their log prefix, set to the
# toolset's name. A toolset script a Dockerfile runs itself (3d's
# build-colmap.sh) sources this file, which then defines them and returns.
#
# APT_DROP_LISTS=true deletes apt's package lists after each toolset's install.
# The cloud Dockerfiles set it: every toolset is its own layer, so each kept
# copy of the lists (about 50 MB) would stay in the image, and sessions there
# can't run apt anyway. The VM images keep them, for sessions' own apt installs.
#
# No apt-get upgrade: each build starts from a fresh ubuntu:24.04, the way the
# macOS image doesn't run softwareupdate. needrestart stays out of the build.
set -euo pipefail

log() { printf '==> %s: %s\n' "${TOOLSET_LOG_NAME:-install-toolset}" "$*"; }
die() {
	printf '==> %s: %s\n' "${TOOLSET_LOG_NAME:-install-toolset}" "$*" >&2
	exit 1
}
# As root (the base's bootstrap, the orchestrator's build) there's no sudo to
# call: the stock Ubuntu image doesn't have it.
apt() {
	local as_root=()
	((EUID == 0)) || as_root=(sudo)
	"${as_root[@]}" env DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get -q "$@"
}

# apt_update: `apt-get update`, run again while the archive is out of sync. A
# repository behind a CDN (NVIDIA's CUDA one, on 2026-11-18) can serve two
# versions of an index for an hour, and an update that meets the other one fails
# with "File has unexpected size ... Mirror sync in progress?" (or "Hash Sum
# mismatch"). The node keeps serving the stale file to that request, so apt's own
# Acquire::Retries can't help: the update has to run again, a few seconds later
# and on another connection. Only those messages retry, so any other failure (an
# unreachable archive, a missing suite) fails at once and a real outage isn't
# hidden. Four attempts, 15, 45 and 90 seconds apart, each failure logged with
# apt's error; the last one fails the build as an update always did, with apt's
# exit status. The one place a cloud build runs `apt-get update` (test_apt_update.py
# checks): the base's bootstrap, every toolset's install and the orchestrator's
# build all call it.
apt_update() {
	local waits=(15 45 90) attempt=1 attempts status line out err
	attempts=$((${#waits[@]} + 1))
	out="$(mktemp)" err="$(mktemp)"
	while :; do
		status=0
		apt update >"$out" 2>"$err" || status=$?
		cat "$out"
		cat "$err" >&2
		((status != 0)) || break
		grep -qE 'Mirror sync in progress|File has unexpected size|Hash Sum mismatch' "$out" "$err" || break
		while read -r line; do
			log "apt_update: attempt $attempt of $attempts failed: $line"
		done < <(grep -hE 'Mirror sync in progress|File has unexpected size|Hash Sum mismatch' "$out" "$err")
		if ((attempt == attempts)); then
			log "apt_update: failed $attempts times in a row, with the archive out of sync"
			break
		fi
		log "apt_update: retrying in ${waits[attempt - 1]}s"
		sleep "${waits[attempt - 1]}"
		attempt=$((attempt + 1))
	done
	rm -f "$out" "$err"
	return "$status"
}

# apt_install_options: the options of every apt install of a package list, one
# per line: each toolset's here, and the base's and the lock's simulation of
# them all in images/cloud/base/apt-prepare.sh, so the lock resolves what the
# builds install. --allow-downgrades: a pin from an older snapshot can be below
# what's installed (images/cloud/base/apt-pins.sh).
apt_install_options() { printf '%s\n' -y --no-install-recommends --allow-downgrades; }

# install_apt_packages <package|.deb>...: one apt install from the snapshot.
install_apt_packages() {
	local options
	(($# > 0)) || return 0
	log "installing $# apt packages"
	mapfile -t options < <(apt_install_options)
	apt_update
	apt install "${options[@]}" "$@"
	apt clean
	if [[ "${APT_DROP_LISTS:-false}" == "true" ]]; then
		sudo rm -rf /var/lib/apt/lists/*
	fi
}

# install_apt <file>: the packages a list names, one per line, with # comments.
install_apt() {
	local packages
	mapfile -t packages < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$1")
	install_apt_packages "${packages[@]}"
}

# github_latest <https://github.com/<owner>/<repo>/releases>: the tag GitHub
# redirects /releases/latest to (no API call, so no rate limit). A release
# tool's `latest` at build, and the lock's resolve and pins (release_tools).
github_latest() {
	local url latest="$1/latest"
	url="$(curl -fsSLI --retry 3 -o /dev/null -w '%{url_effective}' "$latest")" || die "couldn't fetch $latest"
	[[ "$url" == */releases/tag/* ]] || die "no latest release in $1 ($url)"
	printf '%s' "${url##*/}"
}

# Sourced: the helpers are all the caller wanted. Everything above this line
# runs in every sourcing script, scripts/cloud/lock.sh included (the lock watch,
# the deploy's bake-args), so it holds only function definitions: no variable,
# command or cd (test_install_toolset.py checks).
[[ "${BASH_SOURCE[0]}" == "$0" ]] || return 0

toolsets_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly toolsets_dir
readonly brew=/home/linuxbrew/.linuxbrew/bin/brew
# AGENT_TOOLSETS_DIR is the same variable toolset-checks.sh reads (tests set it).
readonly record_dir="${AGENT_TOOLSETS_DIR:-/etc/agent-images/toolsets}"

(($# > 0)) || die "usage: install-toolset.sh <name>..."

# Records an installed toolset: its name once in toolsets.txt, in install order,
# and its CLAUDE.md, check.txt, smoke.sh, profile.sh and path.txt as <name>.md,
# <name>.check, <name>.smoke.sh, <name>.profile.sh and <name>.path.
record() {
	local name="$1" dir="$2" file suffix
	sudo install -d -m 755 "$record_dir"
	sudo touch "$record_dir/toolsets.txt"
	grep -qxF "$name" "$record_dir/toolsets.txt" || printf '%s\n' "$name" | sudo tee -a "$record_dir/toolsets.txt" >/dev/null
	for file in CLAUDE.md:md check.txt:check smoke.sh:smoke.sh profile.sh:profile.sh path.txt:path; do
		suffix="${file#*:}" file="${file%%:*}"
		[[ ! -f "$dir/$file" ]] || sudo install -m 644 "$dir/$file" "$record_dir/$name.$suffix"
	done
}

# Homebrew installs to /home/linuxbrew/.linuxbrew and must not run as root; the
# image's user has sudo.
ensure_brew() {
	local installer url=https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh
	if [[ ! -x "$brew" ]]; then
		log "installing Homebrew"
		# Download to a file first: with bash -c "$(curl ...)" a failed download
		# runs an empty script, which succeeds, and the build dies later on a
		# missing brew, far from the curl error.
		installer="$(mktemp)"
		curl -fsSL -o "$installer" "$url" || die "couldn't download the Homebrew installer from $url"
		# The installer reports routine progress on stderr (fetching
		# Homebrew/brew, portable Ruby, a PATH hint), which would make every
		# image build that runs it not green. Its exit status still fails the
		# build if the install does.
		NONINTERACTIVE=1 /bin/bash "$installer" 2>&1 || die "the Homebrew installer from $url failed"
		rm -f "$installer"
		[[ -x "$brew" ]] || die "the Homebrew installer finished, but $brew isn't there"
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
		# Exported in a subshell, so they reach this install.sh and what it runs,
		# but not the later toolsets' Homebrew and installers.
		(
			export -f log die apt apt_update apt_install_options install_apt install_apt_packages github_latest
			TOOLSET_LOG_NAME="$name" TOOLSET_RECORD_DIR="$record_dir" bash "$dir/install.sh"
		)
		# Any brew command it ran refilled Homebrew's cache.
		[[ ! -x "$brew" ]] || rm -rf "$("$brew" --cache)"
	fi
	record "$name" "$dir"
done
