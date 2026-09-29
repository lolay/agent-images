#!/bin/bash
# Hands Homebrew to the agent user, then installs the Brewfile as that user.
# Homebrew's standard setup is one owning user; making it the agent lets each
# runner upgrade its own CLI and lets sessions install what a project needs.
set -euo pipefail

: "${STAGING_DIR:?}" "${AGENT_USER:?}"

readonly brewfile="$STAGING_DIR/Brewfile"

# sudo keeps admin's working directory, which the agent user can't read.
cd /

sudo chown -R "$AGENT_USER:admin" /opt/homebrew

brew_as_agent() { sudo -u "$AGENT_USER" -H /opt/homebrew/bin/brew "$@"; }

brew_as_agent update --quiet

# Homebrew refuses to load formulae from untrusted third-party taps, so trust
# each Brewfile tap before the bundle loads them.
sed -n 's/^tap "\([^"]*\)".*/\1/p' "$brewfile" | while read -r tap; do
	brew_as_agent tap "$tap"
	brew_as_agent trust --tap "$tap"
done

brew_as_agent bundle --file="$brewfile"
