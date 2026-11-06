#!/bin/bash
# The android toolset's SDK: installs the Android SDK for the image's user and
# builds the emulator it boots in every session, then fails the build if either
# doesn't work. Run by install-toolset.sh after the toolset's apt packages, as
# the image's user; needs the jvm toolset first (and core's curl and unzip).
# It uses the Android image's own files from images/android (setup-user.sh puts
# them in place first): /etc/profile.d/agent-images.sh for ANDROID_HOME, and
# agent-emulator.
#
# Nothing is pinned: the newest command-line tools, and the newest stable API
# level that has both a platform and an x86_64 Google APIs system image, with
# the newest stable build tools and NDK, and the newest Gradle. Packages go
# through Google's Android CLI (`android sdk`, which replaces the deprecated
# sdkmanager). The SDK is owned by the guest user, so sessions can install
# whatever else a project needs.
#
# `android init` installs Google's android-cli agent skill into ~/.claude/skills,
# which the runner seeds into every session.
#
# The NDK is for native code (C++ through CMake, Rust through the android-rust
# toolset's cargo-ndk); its path is recorded as ANDROID_NDK_HOME. A project that
# pins another ndkVersion gets it from the Android Gradle plugin on first build.
# Gradle is for starting a project; a project's builds use its own ./gradlew.
#
# The AVD "agent" is booted once here (the build VM has nested KVM too) and
# saves a quickboot snapshot, so each session's emulator boots in seconds.
# AGENT_EMULATOR_SNAPSHOT=0 skips that step (for testing without KVM).
set -euo pipefail

: "${TOOLSET_RECORD_DIR:?}"

# The environment login shells get: ANDROID_HOME, JAVA_HOME, and PATH.
# shellcheck source=/dev/null
source /etc/profile.d/agent-images.sh

readonly sdk="$ANDROID_HOME"
readonly repository=https://dl.google.com/android/repository
readonly avd=agent

log() { printf '==> android: %s\n' "$*"; }
die() {
	printf '==> android: %s\n' "$*" >&2
	exit 1
}
# The Android CLI, without usage metrics.
android_cli() { android --no-metrics "$@"; }

sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 755 "$sdk"

# The SDK's package list, for the command-line tools zip and the NDK.
repository_xml="$(curl -fsSL "$repository/repository2-3.xml")"

if [[ ! -x "$sdk/cmdline-tools/latest/bin/android" ]]; then
	zip_name="$(printf '%s\n' "$repository_xml" |
		grep -oE 'commandlinetools-linux-[0-9]+_latest\.zip' | sort -t- -k3,3n -u | tail -n 1)"
	[[ -n "$zip_name" ]] || die "no commandlinetools-linux zip in $repository/repository2-3.xml"
	log "bootstrapping $zip_name"
	work="$(mktemp -d)"
	curl -fsSL -o "$work/tools.zip" "$repository/$zip_name"
	unzip -q "$work/tools.zip" -d "$work"
	mkdir -p "$sdk/cmdline-tools"
	rm -rf "$sdk/cmdline-tools/latest"
	mv "$work/cmdline-tools" "$sdk/cmdline-tools/latest"
	rm -rf "$work"
fi

# `android sdk list --all` prints "  <path>  <version>  <description>" rows,
# with paths like platforms/android-37.0 and build-tools/37.0.0. The first run
# also unpacks the CLI and prints its terms; stderr goes with the listing.
available="$(android_cli sdk list --all 2>/dev/null | awk '{print $1}')"
[[ -n "$available" ]] || die "android sdk list returned nothing"
# Stable levels only: 36, 36.1, 37.0 (not -ext or -beta).
api="$(sed -nE 's#^system-images/android-([0-9]+(\.[0-9]+)?)/google_apis/x86_64$#\1#p' <<<"$available" | sort -V |
	while read -r level; do
		grep -qx "platforms/android-$level" <<<"$available" && printf '%s\n' "$level"
	done | tail -n 1)"
[[ -n "$api" ]] || die "no API level with both a platform and a google_apis x86_64 system image"
build_tools="$(grep -E '^build-tools/[0-9]+\.[0-9]+\.[0-9]+$' <<<"$available" | sort -V | tail -n 1)"
[[ -n "$build_tools" ]] || die "no stable build-tools package"
# NDK betas share the ndk;<version> path and channel with releases; only their
# zip names (android-ndk-r30-beta3-linux.zip) tell them apart.
ndk_version="$(printf '%s\n' "$repository_xml" | awk '
	BEGIN { RS = "</remotePackage>" }
	match($0, /path="ndk;[0-9.]+"/) && /android-ndk-r[0-9]+[a-z]?-linux\.zip/ { print substr($0, RSTART + 10, RLENGTH - 11) }' |
	sort -V | tail -n 1)"
[[ -n "$ndk_version" ]] || die "no stable NDK in $repository/repository2-3.xml"
grep -qx "ndk/$ndk_version" <<<"$available" || die "android sdk list has no ndk/$ndk_version"
readonly system_image="system-images/android-$api/google_apis/x86_64"

log "installing platform-tools, emulator, platforms/android-$api, $build_tools, ndk/$ndk_version, $system_image"
# cmdline-tools/latest again: the bootstrap zip carries no package metadata, so
# the SDK lists it as "unknown" until it's installed as a package.
android_cli sdk install cmdline-tools/latest platform-tools emulator "platforms/android-$api" "$build_tools" \
	"ndk/$ndk_version" "$system_image"
[[ -x "$sdk/ndk/$ndk_version/ndk-build" ]] || die "no ndk-build in $sdk/ndk/$ndk_version"
printf 'export ANDROID_NDK_HOME=%s\n' "$sdk/ndk/$ndk_version" |
	sudo tee "$TOOLSET_RECORD_DIR/android.profile.sh" >/dev/null

gradle_url="$(curl -fsSL https://services.gradle.org/versions/current | sed -nE 's/.*"downloadUrl" *: *"([^"]+)".*/\1/p')"
[[ "$gradle_url" == https://*-bin.zip ]] || die "no Gradle download in services.gradle.org/versions/current"
gradle_dir="/opt/gradle/$(basename "$gradle_url" -bin.zip)"
if [[ ! -x "$gradle_dir/bin/gradle" ]]; then
	log "installing $(basename "$gradle_dir")"
	work="$(mktemp -d)"
	curl -fsSL -o "$work/gradle.zip" "$gradle_url"
	sudo install -d -m 755 /opt/gradle
	sudo unzip -q "$work/gradle.zip" -d /opt/gradle
	rm -rf "$work"
fi
sudo ln -sfn "$gradle_dir/bin/gradle" /usr/local/bin/gradle

log "installing the android-cli agent skill"
android_cli init
[[ -f "$HOME/.claude/skills/android-cli/SKILL.md" ]] || die "android init didn't install the android-cli skill"

log "creating AVD $avd ($system_image)"
# avdmanager names packages with semicolons.
echo no | avdmanager create avd --force --name "$avd" --package "${system_image//\//;}" --device pixel_8
config="$HOME/.android/avd/$avd.avd/config.ini"
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

log "verifying"
log "$(java -version 2>&1 | head -n 1)"
log "$(adb version | head -n 1)"
log "$(emulator -version 2>/dev/null | head -n 1)"
log "NDK $ndk_version, $(gradle --version 2>/dev/null | grep -m1 '^Gradle')"
avdmanager list avd -c 2>/dev/null | grep -qx "$avd" || die "AVD $avd isn't listed"

if [[ "${AGENT_EMULATOR_SNAPSHOT:-1}" == "0" ]]; then
	log "skipping the quickboot snapshot (AGENT_EMULATOR_SNAPSHOT=0)"
	exit 0
fi
[[ -e /dev/kvm ]] || die "no /dev/kvm in the build VM: the host needs nested virtualization (make doctor)"
log "booting $avd once to save its quickboot snapshot"
AGENT_EMULATOR_SAVE_SNAPSHOT=1 agent-emulator start
agent-emulator wait
agent-emulator stop
adb kill-server >/dev/null 2>&1 || true
[[ -d "$HOME/.android/avd/$avd.avd/snapshots/default_boot" ]] ||
	die "no quickboot snapshot after the first boot"
log "done: API $api, $build_tools, NDK $ndk_version"
