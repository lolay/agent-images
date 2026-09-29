#!/bin/bash
# Installs the Brewfile as the guest user, who owns the base image's Homebrew
# and its trust store, so each runner can upgrade its own CLI and sessions can
# install what a project needs.
set -euo pipefail

: "${STAGING_DIR:?}"

readonly brewfile="$STAGING_DIR/Brewfile"

# Homebrew first on PATH, as in a login shell, so it doesn't warn that its git
# is shadowed by /usr/bin/git; and no environment-variable hints in build logs.
eval "$(/opt/homebrew/bin/brew shellenv)"
export HOMEBREW_NO_ENV_HINTS=1

# Packer shows stderr in red, and brew update and tap print routine progress
# there (including a note that the base image's tuist tap had local edits it set
# aside). Send those two to stdout; brew bundle keeps stderr, so a real error
# still stands out, and set -e still fails the build on any non-zero exit.
brew update --quiet 2>&1

# Homebrew refuses to load formulae from untrusted third-party taps, so trust
# each Brewfile tap before the bundle loads them.
sed -n 's/^tap "\([^"]*\)".*/\1/p' "$brewfile" | while read -r tap; do
	brew tap "$tap" 2>&1
	brew trust --tap "$tap"
done

# The base image ships the stable claude-code cask, which conflicts with the
# claude-code@latest the Brewfile installs; the Brewfile's channel wins.
if brew list --cask claude-code >/dev/null 2>&1; then
	brew uninstall --cask claude-code
fi

brew bundle --file="$brewfile"
