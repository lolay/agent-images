#!/bin/bash
# Clears the quarantine flag on a Claude Code binary, but only one signed by
# Anthropic. Homebrew quarantines what a cask downloads, so after every brew
# upgrade Gatekeeper asks "downloaded from the Internet, are you sure you want to
# open it?" at claude's next launch, and a LaunchAgent, an SSH session or a VM
# nobody watches can't answer. Homebrew 5 dropped --no-quarantine.
#
# The one check for both sides: the host (clear_claude_quarantine in
# scripts/lib.sh) runs it from the repo, and the macOS guest (runners/claude.sh)
# as ~/bin/agent-clear-quarantine, which images/macos/scripts/setup-user.sh
# installs.
#
# Usage: clear-quarantine.sh <binary>
# Exits 0 when the binary isn't quarantined or the flag is cleared; 1 when the
# signature doesn't verify (the flag stays, and stderr says why) or xattr fails.
set -euo pipefail

# Anthropic's Apple Developer Team ID: the TeamIdentifier and the leaf
# certificate's OU ("Developer ID Application: Anthropic PBC (Q6L2SF6YDW)") of
# the claude the claude-code casks download. To check one:
#   codesign -dv "$(realpath "$(command -v claude)")" 2>&1 | grep TeamIdentifier
readonly ANTHROPIC_TEAM_ID="Q6L2SF6YDW"
# Apple-issued certificate chain (Developer ID) and Anthropic's team.
readonly REQUIREMENT="anchor apple generic and certificate leaf[subject.OU] = \"$ANTHROPIC_TEAM_ID\""

readonly binary="${1:?usage: clear-quarantine.sh <binary>}"

xattr -p com.apple.quarantine "$binary" >/dev/null 2>&1 || exit 0
if ! codesign --verify --strict -R="$REQUIREMENT" "$binary"; then
	printf "%s is quarantined and its signature doesn't verify as Anthropic's (team %s), so it stays quarantined\n" \
		"$binary" "$ANTHROPIC_TEAM_ID" >&2
	exit 1
fi
xattr -d com.apple.quarantine "$binary"
printf "cleared Homebrew's quarantine on %s\n" "$binary"
