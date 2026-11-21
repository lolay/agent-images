#!/bin/bash
# The jvm toolset's environment: records where the JDK packages.txt installed
# lives, as JAVA_HOME and its bin at the front of PATH, so every session's java,
# javac and Gradle use that JDK whatever installs another (the android toolset's
# JDK 17). Read from the package's own files, not the javac first on PATH: that
# can be another JDK (a hosted runner's Temurin, a Homebrew openjdk), and the
# directory's name carries the architecture (java-21-openjdk-amd64, -arm64).
# Run by install-toolset.sh after the toolset's apt packages; log, die and
# package_list are its.
set -euo pipefail

: "${TOOLSET_RECORD_DIR:?}"

mapfile -t packages < <(package_list "$(dirname "${BASH_SOURCE[0]}")/packages.txt")
((${#packages[@]} > 0)) || die "packages.txt names no JDK"
files="$(dpkg -L "${packages[@]}")" || die "dpkg can't list ${packages[*]}: is the JDK installed?"
javac="$(grep '/bin/javac$' <<<"$files" || true)"
[[ -n "$javac" && "$javac" != *$'\n'* ]] ||
	die "expected one bin/javac in ${packages[*]}, found: ${javac:-none}"
java_home="${javac%/bin/javac}"
[[ -x "$java_home/bin/java" ]] || die "no java beside $javac"
# A default, not an override: the cloud images source the profile again in
# every bash (BASH_ENV), and a session that points JAVA_HOME at another JDK
# keeps it in the scripts it runs.
# shellcheck disable=SC2016 # the record expands it, not this
printf 'export JAVA_HOME="${JAVA_HOME:-%s}"\n' "$java_home" |
	sudo tee "$TOOLSET_RECORD_DIR/jvm.profile.sh" >/dev/null
printf '%s/bin\n' "$java_home" | sudo tee "$TOOLSET_RECORD_DIR/jvm.path" >/dev/null
log "JAVA_HOME $java_home: $("$java_home/bin/java" -version 2>&1 | head -n 1)"
