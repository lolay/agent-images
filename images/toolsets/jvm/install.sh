#!/bin/bash
# The jvm toolset's environment: records where the JDK packages.txt installed
# lives, as JAVA_HOME's default, with JAVA_HOME's bin at the front of PATH, so
# every session's java, javac and Gradle use one JDK: that one, whatever installs
# another (the android toolset's JDK 17), or the one a session points JAVA_HOME
# at. Read from the package's own files, not the javac first on PATH: that can
# be another JDK (a hosted runner's Temurin, a Homebrew openjdk), and the
# directory's name carries the architecture (java-21-openjdk-amd64, -arm64).
# Run by install-toolset.sh after the toolset's apt packages; log, die and
# apt_list are its.
set -euo pipefail

: "${TOOLSET_RECORD_DIR:?}"

# Assigned first: a list apt_list can't read fails here rather than as no JDK.
list="$(apt_list "$(dirname "${BASH_SOURCE[0]}")/packages.txt")" || die "can't read the jvm packages.txt"
mapfile -t packages <<<"$list"
[[ -n "${packages[0]:-}" ]] || packages=()
((${#packages[@]} > 0)) || die "packages.txt names no JDK"
files="$(dpkg -L "${packages[@]}")" || die "dpkg can't list ${packages[*]}: is the JDK installed?"
javac="$(grep '/bin/javac$' <<<"$files" || true)"
[[ -n "$javac" && "$javac" != *$'\n'* ]] ||
	die "expected one bin/javac in ${packages[*]}, found: ${javac:-none}"
java_home="${javac%/bin/javac}"
[[ -x "$java_home/bin/java" ]] || die "no java beside $javac"
# JAVA_HOME is a default, not an override: the cloud images source the profile
# again in every bash (BASH_ENV), and a session that points JAVA_HOME at another
# JDK keeps it in the scripts it runs. Its bin goes first on PATH, ahead of ~/bin
# (it holds only the JDK's tools), once (any other copy goes), and only while
# PATH is being set up (agent_set_path, toolset-profile.sh), so it's the bin of
# the JAVA_HOME the shell starts with. Shell only, no subprocess: every bash
# sources it.
{
	# shellcheck disable=SC2016 # the record expands it, not this
	printf 'export JAVA_HOME="${JAVA_HOME:-%s}"\n' "$java_home"
	cat <<'PROFILE'
if [ -n "${agent_set_path:-}" ]; then
	agent_jdk_bin="$JAVA_HOME/bin" agent_jdk_left="$PATH:" agent_jdk_path="$JAVA_HOME/bin"
	while [ -n "$agent_jdk_left" ]; do
		agent_jdk_dir="${agent_jdk_left%%:*}"
		agent_jdk_left="${agent_jdk_left#*:}"
		[ "$agent_jdk_dir" = "$agent_jdk_bin" ] || agent_jdk_path="$agent_jdk_path:$agent_jdk_dir"
	done
	PATH="$agent_jdk_path"
	unset agent_jdk_bin agent_jdk_left agent_jdk_path agent_jdk_dir
fi
PROFILE
} | sudo tee "$TOOLSET_RECORD_DIR/jvm.profile.sh" >/dev/null
log "JAVA_HOME $java_home: $("$java_home/bin/java" -version 2>&1 | head -n 1)"
