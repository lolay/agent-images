#!/bin/bash
# Makes sure Xcode is ready for the guest user and fails the build if not. The
# base image follows :latest, so this checks rather than assumes. Every step is
# a no-op when already done. Runs in the guest as that user (passwordless sudo),
# after install-packages.sh (needs jq).
#
# Machine-wide: an Xcode.app selected (its developer dir provides the command
# line tools, so no separate CLT package is needed), license accepted, first
# launch done, an iOS simulator runtime, developer tools security, and
# automation mode for macOS UI tests. Per user: membership in _developer and an
# iPhone simulator, which agent-runner-claude also ensures at every start.
set -euo pipefail

: "${GUEST_USER:?}"

eval "$(/opt/homebrew/bin/brew shellenv)"

log() { printf '==> ensure-xcode: %s\n' "$*"; }
# A login shell, so the user's PATH (~/.zprofile) applies, as it does at runtime.
login_shell() { /bin/zsh -lc "$1"; }

developer_dir="$(xcode-select -p)"
if [[ "$developer_dir" != */Xcode*.app/Contents/Developer ]]; then
	xcode_app="$(find /Applications -maxdepth 1 -name 'Xcode*.app' | sort | tail -n 1)"
	[[ -n "$xcode_app" ]] || {
		echo "no Xcode*.app in /Applications and xcode-select points at $developer_dir" >&2
		exit 1
	}
	log "selecting $xcode_app (was $developer_dir)"
	sudo xcode-select -s "$xcode_app/Contents/Developer"
fi
log "using $(xcode-select -p)"

log "accepting the license and running first launch"
sudo xcodebuild -license accept
sudo xcodebuild -runFirstLaunch

if ! xcrun simctl list runtimes available | grep -q '^iOS '; then
	log "no iOS simulator runtime; downloading it"
	xcodebuild -downloadPlatform iOS
fi

log "enabling developer tools for $GUEST_USER"
sudo DevToolsSecurity -enable
sudo dseditgroup -o edit -a "$GUEST_USER" -t user _developer
# macOS UI tests (XCUITest on a Mac app) otherwise stop at an authentication prompt.
# It prints "Enter the password for user 'root':" even as root, then succeeds:
# stdin from /dev/null so it can never wait on that prompt, and the prompt text
# dropped from the build log. Its exit status still decides.
automation_output="$(sudo automationmodetool enable-automationmode-without-authentication </dev/null 2>&1)"
printf '%s\n' "${automation_output//Enter the password for user \'root\':/}"

log "verifying as $GUEST_USER"
login_shell 'xcodebuild -version'
login_shell 'xcrun --sdk macosx --show-sdk-path'
login_shell 'xcrun --sdk iphonesimulator --show-sdk-path'
login_shell 'xcrun simctl list runtimes available | grep "^iOS "'
dseditgroup -o checkmember -m "$GUEST_USER" _developer

# Packer's SSH session isn't the GUI session, so CoreSimulator may not serve it;
# agent-runner-claude retries in the real session at every start.
if ! login_shell 'agent-ensure-simulator'; then
	log "warning: couldn't create an iPhone simulator at build time; the runner retries at start"
fi
