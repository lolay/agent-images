#!/bin/bash
# apt pins that name their own Ubuntu snapshot (specs/gcp.md, "Pinning a
# dependency"): a pin's version can outlive the type's snapshot, whose index
# lists only each pocket's current version, so the pin's snapshot is added as an
# extra source and apt's preferences make that version the candidate for that
# package alone. Every other package still comes from the type's snapshot: an
# older snapshot holds no newer version of anything.
#
#   apt-pins.sh install [<name>=<version>@<snapshot>...]
#       one source per snapshot, one preference per package; with no pins,
#       nothing (and no files). With APT_SNAPSHOT set (the build's), a pin's
#       snapshot newer than it is refused: the source is the snapshot's whole
#       index, which would then offer newer versions of every other package.
#   apt-pins.sh remove
#       deletes both files
#
# The pins are read from the type's package lists by lock.sh: `bake-args` gives
# the build APT_PINS, and `resolve` passes the same list to its container.
# Run as root by images/cloud/base/apt-prepare.sh (install), in the base
# Dockerfile's apt layer and in lock.sh resolve_apt's container alike, and by
# images/cloud/runner/Dockerfile (remove, with the build's other apt settings).
#
# APT_PINS_ROOT prefixes every path (tests).
set -euo pipefail

readonly root="${APT_PINS_ROOT:-}"
readonly sources="$root/etc/apt/sources.list.d/agent-pins.sources"
readonly preferences="$root/etc/apt/preferences.d/agent-pins"
readonly snapshots=https://snapshot.ubuntu.com/ubuntu
readonly keyring=/usr/share/keyrings/ubuntu-archive-keyring.gpg

die() {
	printf 'apt-pins: %s\n' "$*" >&2
	exit 1
}

install_pins() {
	local pin name version snapshot codename
	local -a ids=()
	local -A seen=()
	(($# > 0)) || return 0
	# Every pin checked before anything is written.
	for pin in "$@"; do
		[[ "$pin" =~ ^([a-z0-9][a-z0-9.+-]*)=([^=@[:space:]]+)@([0-9]{8}T[0-9]{6}Z)$ ]] ||
			die "'$pin' isn't <name>=<version>@<snapshot>"
		name="${BASH_REMATCH[1]}" snapshot="${BASH_REMATCH[3]}"
		[[ -z "${APT_SNAPSHOT:-}" ]] || ! [[ "$snapshot" > "$APT_SNAPSHOT" ]] ||
			die "$pin: $snapshot is newer than the build's snapshot, $APT_SNAPSHOT (lock.sh resolve moves it)"
		[[ -z "${seen[$name]:-}" ]] || die "$name is pinned twice"
		seen[$name]=1
	done
	# shellcheck disable=SC1091 # the image's own
	codename="$(. "$root/etc/os-release" && printf '%s' "${VERSION_CODENAME:-}")"
	[[ -n "$codename" ]] || die "no VERSION_CODENAME in $root/etc/os-release"
	mkdir -p "$(dirname "$sources")" "$(dirname "$preferences")"
	: >"$sources"
	: >"$preferences"
	for pin in "$@"; do
		[[ "$pin" =~ ^(.+)=(.+)@(.+)$ ]]
		name="${BASH_REMATCH[1]}" version="${BASH_REMATCH[2]}" snapshot="${BASH_REMATCH[3]}"
		printf 'Package: %s\nPin: version %s\nPin-Priority: 1001\n\n' "$name" "$version" >>"$preferences"
		[[ " ${ids[*]} " == *" $snapshot "* ]] || ids+=("$snapshot")
	done
	# The snapshot's own URL, with Snapshot: no: with APT::Snapshot set, apt
	# would otherwise move this source to the type's snapshot too, through the
	# Snapshots field of the Release file it downloads.
	for snapshot in "${ids[@]}"; do
		printf 'Types: deb\nURIs: %s/%s/\nSuites: %s %s-updates %s-security\nComponents: main restricted universe multiverse\nSigned-By: %s\nSnapshot: no\n\n' \
			"$snapshots" "$snapshot" "$codename" "$codename" "$codename" "$keyring" >>"$sources"
	done
}

case "${1:-}" in
install)
	shift
	install_pins "$@"
	;;
remove)
	(($# == 1)) || die "usage: apt-pins.sh remove"
	rm -f "$sources" "$preferences"
	;;
*) die "usage: apt-pins.sh install [<name>=<version>@<snapshot>...] | remove" ;;
esac
