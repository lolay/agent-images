#!/bin/bash
# The android-go toolset: gomobile and gobind, which build a Go package into an
# Android library (.aar) with the NDK the android toolset installed. Needs the go
# and android toolsets first. Run by install-toolset.sh, as the image's user.
#
# golang.org/x/mobile has no tagged releases and calls itself experimental, so
# @latest is its newest commit.
set -euo pipefail

# The go toolset's install bin (~/go/bin), where gomobile lands, and the android
# toolset's ANDROID_HOME and ANDROID_NDK_HOME, which gomobile init reads. go is
# Homebrew's, on PATH already (install-toolset.sh).
toolset_env go android

# Into ~/go/bin, which the go toolset's profile puts on every session's PATH.
go install golang.org/x/mobile/cmd/gomobile@latest golang.org/x/mobile/cmd/gobind@latest 2>&1
gomobile init 2>&1
log "gomobile $(go version -m "$(command -v gomobile)" | awk '$1 == "mod" { print $3 }')"
