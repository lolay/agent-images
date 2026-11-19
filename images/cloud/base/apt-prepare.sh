#!/bin/bash
# The apt bootstrap of every Cloud Run image, and the lock's simulation of it
# (specs/gcp.md, "The lock"): one sequence, run by both, so the lock resolves
# what the build installs.
#
#   apt-prepare.sh <package>...
#       the build: images/cloud/base/Dockerfile's apt layer installs the base's
#       packages from the snapshot APT_SNAPSHOT names (required), and leaves
#       the settings for every toolset layer after it
#   apt-prepare.sh --simulate <package>...
#       the resolve (scripts/cloud/lock.sh resolve_apt): the same steps in a
#       stock container, against today's archive (no APT_SNAPSHOT: the resolve
#       picks the snapshot), then prints `apt-get install --simulate` of the
#       packages, every type's list, from the package state saved before the
#       first step's install
#
# The steps:
#   1. apt-build.conf as /etc/apt/apt.conf.d/40agent-build: a part of the
#      archive that fails to download fails each update after it
#   2. apt_update, then ca-certificates (on plain Ubuntu with its openssl)
#      from today's archive: snapshot.ubuntu.com is HTTPS only, and the stock
#      image has no CA certificates
#   3. the build alone: APT::Snapshot at APT_SNAPSHOT
#      (/etc/apt/apt.conf.d/50agent-snapshot)
#   4. apt-pins.sh install $APT_PINS: each pin's own snapshot as a source, its
#      version the candidate. APT_PINS is lock.sh bake-args' (the build's) or
#      resolve_apt's, both read from the type's package lists
#   5. apt_update
#   6. the install, or its simulation
# apt_update is install-toolset.sh's, the one apt-get update of the cloud builds:
# it runs the update again while the archive is out of sync (its header has the
# rules), so a repository mid-sync doesn't fail the build or the resolve.
# Every install, the simulation's included, takes install-toolset.sh's
# apt_install_options, which every toolset layer's install takes too: with -y,
# apt refuses a downgrade --allow-downgrades doesn't allow, so a pin below
# what's installed (openssl, after step 2) resolves only as the build installs it.
#
# apt-build.conf, apt-pins.sh and install-toolset.sh are read from beside this
# script: the base Dockerfile and resolve_apt mount all four in /tmp. Run as
# root. APT_PREPARE_ROOT prefixes the paths it reads and writes (tests).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly here
# shellcheck source=images/toolsets/install-toolset.sh
source "$here/install-toolset.sh"
TOOLSET_LOG_NAME=apt-prepare

readonly root="${APT_PREPARE_ROOT:-}"
readonly conf_dir="$root/etc/apt/apt.conf.d"

die() {
	printf 'apt-prepare: %s\n' "$*" >&2
	exit 1
}
usage() { die "usage: apt-prepare.sh [--simulate] <package>..."; }

simulate=false
if [[ "${1:-}" == --simulate ]]; then
	simulate=true
	shift
fi
(($# > 0)) || usage
[[ "$1" != -* ]] || usage
if $simulate; then
	[[ -z "${APT_SNAPSHOT:-}" ]] || die "--simulate reads today's archive; APT_SNAPSHOT is the build's"
else
	[[ -n "${APT_SNAPSHOT:-}" ]] || die "no APT_SNAPSHOT: the build passes the lock's apt-snapshot (scripts/cloud/lock.sh bake-args)"
fi
read -ra pins <<<"${APT_PINS:-}"
mapfile -t options < <(apt_install_options)
export DEBIAN_FRONTEND=noninteractive

# step <command>...: one step before the install. The build shows its output;
# the simulation's stdout is apt's answer alone, so there a step's output is
# shown only when it fails.
step_log="$(mktemp)"
readonly step_log
trap 'rm -f "$step_log" "${saved_status:-}"' EXIT
step() {
	if ! $simulate; then
		"$@"
	elif ! "$@" >"$step_log" 2>&1; then
		cat "$step_log" >&2
		die "failed: $*"
	fi
}

if $simulate; then
	saved_status="$(mktemp)"
	cp "$root/var/lib/dpkg/status" "$saved_status"
fi
step install -D -m 644 "$here/apt-build.conf" "$conf_dir/40agent-build"
step apt_update
step apt-get install "${options[@]}" ca-certificates
if ! $simulate; then
	printf 'APT::Snapshot "%s";\n' "$APT_SNAPSHOT" >"$conf_dir/50agent-snapshot"
fi
step bash "$here/apt-pins.sh" install "${pins[@]}"
step apt_update
if $simulate; then
	apt-get install "${options[@]}" --simulate -o Dir::State::status="$saved_status" "$@"
else
	apt-get install "${options[@]}" "$@"
fi
