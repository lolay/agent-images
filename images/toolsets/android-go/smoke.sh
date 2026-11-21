#!/bin/bash
# The android-go toolset's smoke test (specs/gcp.md, m4): go build cross-compiles
# a cgo hello for Android arm64 (phones) and amd64 (the emulator's x86_64) with
# the NDK's clang (the android toolset's ANDROID_NDK_HOME). Compile only: nothing
# runs them.
set -euo pipefail

die() {
	echo "android-go: $*"
	exit 1
}

[[ -n "${ANDROID_NDK_HOME:-}" ]] || die "ANDROID_NDK_HOME isn't set (the android toolset records it in the profile)"
[[ -d "$ANDROID_NDK_HOME" ]] || die "ANDROID_NDK_HOME=$ANDROID_NDK_HOME isn't a directory"
bin="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin"

cat >go.mod <<'GOMOD'
module hello

go 1.20
GOMOD
cat >main.go <<'GO'
package main

// static int answer(void) { return 42; }
import "C"

import "fmt"

func main() { fmt.Println("hello from go", C.answer()) }
GO

# build <GOARCH> <clang triple> <file's description of the machine>
build() {
	local goarch="$1" triple="$2" machine="$3" clang kind
	# The lowest API level the NDK still has a clang wrapper for.
	clang="$(printf '%s\n' "$bin/$triple"*-clang | sort -V | sed -n 1p)"
	[[ -x "$clang" ]] || die "no $triple<API>-clang in $bin"
	api="${clang##*android}"
	api="${api%-clang}"
	CGO_ENABLED=1 GOOS=android GOARCH="$goarch" CC="$clang" go build -o "hello-$goarch" . >build.log 2>&1 || {
		tail -n 20 build.log
		die "go build for android/$goarch with $clang failed"
	}
	kind="$(file -b "hello-$goarch")"
	[[ "$kind" == "ELF 64-bit LSB"*"$machine"* ]] ||
		die "hello-$goarch is '$kind', expected an ELF 64-bit $machine binary"
}

build arm64 aarch64-linux-android "ARM aarch64"
build amd64 x86_64-linux-android "x86-64"
echo "android-go: built a cgo hello for android/arm64 and android/amd64 with the API $api clang"
