#!/bin/bash
# Installs the Android SDK for the agent user and builds the emulator it boots
# in every session, then fails the build if either doesn't work. Runs in the
# guest as root, after install-packages.sh (needs the JDK, curl, unzip).
#
# Nothing is pinned: the newest command-line tools, and the newest stable API
# level that has both a platform and an x86_64 Google APIs system image, with
# the newest stable build tools. Packages go through Google's Android CLI
# (`android sdk`, which replaces the deprecated sdkmanager). The SDK is owned by
# agent, so sessions can install whatever else a project needs.
#
# `android init` installs Google's android-cli agent skill into ~/.claude/skills,
# which the runner seeds into every session.
#
# The AVD "agent" is booted once here (the build VM has nested KVM too) and
# saves a quickboot snapshot, so each session's emulator boots in seconds.
# AGENT_EMULATOR_SNAPSHOT=0 skips that step (for testing without KVM).
set -euo pipefail

: "${AGENT_USER:?}"

readonly sdk=/opt/android-sdk
readonly repository=https://dl.google.com/android/repository
readonly avd=agent
readonly home="/home/$AGENT_USER"

log() { printf '==> ensure-android-sdk: %s\n' "$*"; }
die() {
	printf '==> ensure-android-sdk: %s\n' "$*" >&2
	exit 1
}
# A login shell, so the agent's environment (/etc/profile.d) applies, as it does
# at runtime.
as_agent() { sudo -u "$AGENT_USER" -H bash -lc "$1"; }
# The Android CLI, without usage metrics; its JVM's banner goes to stderr.
android_cli() { as_agent "android --no-metrics $1"; }

install -d -o "$AGENT_USER" -g "$AGENT_USER" -m 755 "$sdk"

if [[ ! -x "$sdk/cmdline-tools/latest/bin/android" ]]; then
	zip_name="$(curl -fsSL "$repository/repository2-3.xml" |
		grep -oE 'commandlinetools-linux-[0-9]+_latest\.zip' | sort -t- -k3,3n -u | tail -n 1)"
	[[ -n "$zip_name" ]] || die "no commandlinetools-linux zip in $repository/repository2-3.xml"
	log "bootstrapping $zip_name"
	work="$(mktemp -d)"
	curl -fsSL -o "$work/tools.zip" "$repository/$zip_name"
	unzip -q "$work/tools.zip" -d "$work"
	install -d -o "$AGENT_USER" -g "$AGENT_USER" "$sdk/cmdline-tools"
	rm -rf "$sdk/cmdline-tools/latest"
	mv "$work/cmdline-tools" "$sdk/cmdline-tools/latest"
	chown -R "$AGENT_USER:$AGENT_USER" "$sdk/cmdline-tools"
	rm -rf "$work"
fi

# `android sdk list --all` prints "  <path>  <version>  <description>" rows,
# with paths like platforms/android-37.0 and build-tools/37.0.0.
available="$(android_cli 'sdk list --all' 2>/dev/null | awk '{print $1}')"
[[ -n "$available" ]] || die "android sdk list returned nothing"
# Stable levels only: 36, 36.1, 37.0 (not -ext or -beta).
api="$(sed -nE 's#^system-images/android-([0-9]+(\.[0-9]+)?)/google_apis/x86_64$#\1#p' <<<"$available" | sort -V |
	while read -r level; do
		grep -qx "platforms/android-$level" <<<"$available" && printf '%s\n' "$level"
	done | tail -n 1)"
[[ -n "$api" ]] || die "no API level with both a platform and a google_apis x86_64 system image"
build_tools="$(grep -E '^build-tools/[0-9]+\.[0-9]+\.[0-9]+$' <<<"$available" | sort -V | tail -n 1)"
[[ -n "$build_tools" ]] || die "no stable build-tools package"
readonly system_image="system-images/android-$api/google_apis/x86_64"

log "installing platform-tools, emulator, platforms/android-$api, $build_tools, $system_image"
# cmdline-tools/latest again: the bootstrap zip carries no package metadata, so
# the SDK lists it as "unknown" until it's installed as a package.
android_cli "sdk install cmdline-tools/latest platform-tools emulator platforms/android-$api $build_tools $system_image"

log "installing the android-cli agent skill"
android_cli init
[[ -f "$home/.claude/skills/android-cli/SKILL.md" ]] || die "android init didn't install the android-cli skill"

log "creating AVD $avd ($system_image)"
# avdmanager names packages with semicolons.
as_agent "echo no | avdmanager create avd --force --name $avd --package '${system_image//\//;}' --device pixel_8"
config="$home/.android/avd/$avd.avd/config.ini"
[[ -f "$config" ]] || die "avdmanager didn't create $config"
# 4 GB is the emulator's minimum from API 37; room for installs and test data.
set_config() {
	sed -i "/^$1=/d" "$config"
	printf '%s=%s\n' "$1" "$2" >>"$config"
}
set_config hw.ramSize 4096
set_config disk.dataPartition.size 8G
set_config hw.keyboard yes
set_config hw.audioInput no
set_config hw.audioOutput no

log "verifying as $AGENT_USER"
as_agent 'java -version 2>&1 | head -n 1'
as_agent 'adb version | head -n 1'
as_agent 'emulator -version 2>/dev/null | head -n 1'
as_agent 'avdmanager list avd -c 2>/dev/null' | grep -qx "$avd" || die "AVD $avd isn't listed"

if [[ "${AGENT_EMULATOR_SNAPSHOT:-1}" == "0" ]]; then
	log "skipping the quickboot snapshot (AGENT_EMULATOR_SNAPSHOT=0)"
	exit 0
fi
[[ -e /dev/kvm ]] || die "no /dev/kvm in the build VM: the host needs nested virtualization (make doctor)"
log "booting $avd once to save its quickboot snapshot"
as_agent 'AGENT_EMULATOR_SAVE_SNAPSHOT=1 agent-emulator start && agent-emulator wait && agent-emulator stop'
as_agent 'adb kill-server' || true
[[ -d "$home/.android/avd/$avd.avd/snapshots/default_boot" ]] ||
	die "no quickboot snapshot after the first boot"
log "done: API $api, $build_tools"
