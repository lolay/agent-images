#!/bin/bash
# The android-go toolset: gomobile and gobind, which build a Go package into an
# Android library (.aar) with the NDK the android toolset installed. Needs the go
# and android toolsets first. Run by install-toolset.sh, as the image's user.
#
# golang.org/x/mobile has no tagged releases and calls itself experimental, so
# @latest is its newest commit.
set -euo pipefail

# ANDROID_HOME and ANDROID_NDK_HOME, which gomobile init reads.
# shellcheck source=/dev/null
source /etc/profile.d/agent-images.sh
PATH="/home/linuxbrew/.linuxbrew/bin:$(go env GOPATH)/bin:$PATH"

# Into $(go env GOPATH)/bin (~/go/bin), which the image's profile puts on PATH.
go install golang.org/x/mobile/cmd/gomobile@latest golang.org/x/mobile/cmd/gobind@latest 2>&1
gomobile init 2>&1
printf '==> install-toolset: gomobile %s\n' "$(go version -m "$(command -v gomobile)" | awk '$1 == "mod" { print $3 }')"
