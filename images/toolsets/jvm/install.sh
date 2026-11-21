#!/bin/bash
# The jvm toolset's environment: records where the JDK packages.txt installed
# lives, as JAVA_HOME and its bin at the front of PATH, so every session's java,
# javac and Gradle use that JDK whatever installs another (the android toolset's
# JDK 17). Read from the javac apt put on PATH, since the directory's name
# carries the architecture (java-21-openjdk-amd64, -arm64). Run by
# install-toolset.sh after the toolset's apt packages; log and die are its.
set -euo pipefail

: "${TOOLSET_RECORD_DIR:?}"

javac="$(command -v javac)" || die "no javac on PATH after the JDK's install"
java_home="$(readlink -f "$javac")"
java_home="${java_home%/bin/javac}"
[[ -x "$java_home/bin/java" ]] || die "no java beside $javac (in $java_home/bin)"
# A default, not an override: the cloud images source the profile again in
# every bash (BASH_ENV), and a session that points JAVA_HOME at another JDK
# keeps it in the scripts it runs.
# shellcheck disable=SC2016 # the record expands it, not this
printf 'export JAVA_HOME="${JAVA_HOME:-%s}"\n' "$java_home" |
	sudo tee "$TOOLSET_RECORD_DIR/jvm.profile.sh" >/dev/null
printf '%s/bin\n' "$java_home" | sudo tee "$TOOLSET_RECORD_DIR/jvm.path" >/dev/null
log "JAVA_HOME $java_home: $("$java_home/bin/java" -version 2>&1 | head -n 1)"
