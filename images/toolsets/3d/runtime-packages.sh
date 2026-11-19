#!/bin/bash
# write_runtime_packages: what build-colmap.sh ends with, sourced from it (and
# from tests/unit/test_install_toolset.py, which fakes ldd and dpkg). Needs
# install-toolset.sh's log and die sourced first.
#
# The list is the packages the built binary links, for the 3d toolset's
# install.sh to install: those the build list installed, at the version
# installed here (so the image runs COLMAP with what it was built against,
# pins included), and those the base image already had by name, since a later
# toolset may move them within the snapshot.

# installed_packages: every installed package as name=version, sorted.
# shellcheck disable=SC2016 # dpkg-query's own ${field} syntax, not the shell's
installed_packages() { dpkg-query -W -f='${Package}=${Version}\n' | LC_ALL=C sort; }

# packages_changed_since <installed_packages output>: what has been installed or
# changed since then, as name=version lines.
packages_changed_since() {
	LC_ALL=C comm -13 <(printf '%s\n' "$1") <(installed_packages)
}

# owner <library>: the package that owns it. dpkg records some libraries under
# /usr/lib and some under /lib, and ldd's path can be either.
owner() {
	local real
	real="$(readlink -f "$1")"
	dpkg -S "$real" 2>/dev/null || dpkg -S "${real#/usr}" 2>/dev/null ||
		die "no package owns $1 ($real)"
}

# write_runtime_packages <binary> <list> <from_build_list>: <list> gets one line
# per package the binary links: `name=version` if <from_build_list> (the
# `name=version` lines the build list's install added or changed) has it, else
# the bare name. Fails if a library isn't installed or no package owns one.
write_runtime_packages() {
	local bin="$1" list="$2" from_build_list="$3" owners_file owners package built lib
	if ldd "$bin" | grep -q 'not found'; then
		ldd "$bin" >&2
		die "colmap links a library that isn't installed"
	fi
	sudo install -d -m 755 "$(dirname "$list")"
	# Through a file, not <(...): owner's die has to stop the build (pipefail).
	owners_file="$(mktemp)"
	ldd "$bin" | awk '/=> \// {print $3}' | while read -r lib; do
		owner "$lib"
	done | cut -d: -f1 | LC_ALL=C sort -u >"$owners_file"
	mapfile -t owners <"$owners_file"
	rm -f "$owners_file"
	((${#owners[@]} > 0)) || die "found no packages for $bin's libraries"
	# Pinned to the built version if the build list installed it; by name if the
	# base image had it (that version may not be in the snapshot at all).
	for package in "${owners[@]}"; do
		built="$(awk -F= -v name="$package" '$1 == name {print; exit}' <<<"$from_build_list")"
		printf '%s\n' "${built:-$package}"
	done | sudo tee "$list" >/dev/null
	log "runtime packages: $(paste -sd ' ' "$list")"
}
