#!/bin/bash
# Makes sure Xcode is ready for the agent user, then proves it as that user and
# fails the build if not. The base image follows :latest, so this checks rather
# than assumes. Every step is a no-op when already done. Runs in the guest as
# admin (passwordless sudo), after install-packages.sh (needs jq).
#
# Machine-wide: an Xcode.app selected (its developer dir provides the command
# line tools, so no separate CLT package is needed), license accepted, first
# launch done, an iOS simulator runtime, developer tools security, automation
# mode for macOS UI tests, and agent in _developer. Per user: an iPhone
# simulator, which agent-runner-claude also ensures at every start.
set -euo pipefail

: "${AGENT_USER:?}"

eval "$(/opt/homebrew/bin/brew shellenv)"

log() { printf '==> ensure-xcode: %s\n' "$*"; }
# A login shell, so the agent's PATH (~/.zprofile) applies, as it does at runtime.
as_agent() { sudo -u "$AGENT_USER" -H /bin/zsh -lc "$1"; }

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

log "enabling developer tools for $AGENT_USER"
sudo DevToolsSecurity -enable
sudo dseditgroup -o edit -a "$AGENT_USER" -t user _developer
# macOS UI tests (XCUITest on a Mac app) otherwise stop at an authentication prompt.
sudo automationmodetool enable-automationmode-without-authentication

log "verifying as $AGENT_USER"
as_agent 'xcodebuild -version'
as_agent 'xcrun --sdk macosx --show-sdk-path'
as_agent 'xcrun --sdk iphonesimulator --show-sdk-path'
as_agent 'xcrun simctl list runtimes available | grep "^iOS "'
dseditgroup -o checkmember -m "$AGENT_USER" _developer

# agent has no login session during the build, so CoreSimulator may not serve it
# yet; agent-runner-claude retries in the real session at every start.
if ! as_agent 'agent-ensure-simulator'; then
	log "warning: couldn't create $AGENT_USER's iPhone simulator at build time; the runner retries at start"
fi
