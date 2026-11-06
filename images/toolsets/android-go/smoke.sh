#!/bin/bash
# The android-go toolset's smoke test (specs/gcp.md, m4): go build cross-compiles
# a cgo hello for Android arm64 with the NDK's clang (the android toolset's
# ANDROID_NDK_HOME). Compile only: there is no device to run it on.
set -euo pipefail

die() {
	echo "android-go: $*"
	exit 1
}

[[ -n "${ANDROID_NDK_HOME:-}" ]] || die "ANDROID_NDK_HOME isn't set (the android toolset records it in the profile)"
[[ -d "$ANDROID_NDK_HOME" ]] || die "ANDROID_NDK_HOME=$ANDROID_NDK_HOME isn't a directory"
bin="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin"
# The lowest API level the NDK still has a clang wrapper for.
clang="$(printf '%s\n' "$bin"/aarch64-linux-android*-clang | sort -V | sed -n 1p)"
[[ -x "$clang" ]] || die "no aarch64-linux-android<API>-clang in $bin"
api="${clang##*android}"
api="${api%-clang}"

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
CGO_ENABLED=1 GOOS=android GOARCH=arm64 CC="$clang" go build -o hello . >build.log 2>&1 || {
	tail -n 20 build.log
	die "go build for android/arm64 with $clang failed"
}
kind="$(file -b hello)"
[[ "$kind" == "ELF 64-bit LSB"*"ARM aarch64"* ]] || die "hello is '$kind', expected an ELF 64-bit ARM aarch64 binary"
echo "android-go: built a cgo hello for android/arm64 with the API $api clang"
