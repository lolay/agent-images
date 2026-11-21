#!/bin/bash
# The android-rust toolset's smoke test (specs/gcp.md, m4): cargo-ndk
# cross-compiles a Rust cdylib for arm64-v8a (phones) and x86_64 (the emulator)
# with the NDK, offline. Compile only; nothing runs them.
set -euo pipefail

[[ -n "${ANDROID_NDK_HOME:-}" && -d "$ANDROID_NDK_HOME" ]] || {
	echo "android-rust: ANDROID_NDK_HOME is not set to a directory (got '${ANDROID_NDK_HOME:-}')"
	exit 1
}
mkdir -p hello/src
cat >hello/Cargo.toml <<'TOML'
[package]
name = "hello"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]
TOML
cat >hello/src/lib.rs <<'RUST'
#[no_mangle]
pub extern "C" fn hello() -> i32 {
    42
}
RUST
# cargo-ndk's own flags come before `build`; everything after it goes to cargo.
# Its output goes to a log, shown only if the build fails.
if ! (cd hello && cargo ndk -t arm64-v8a -t x86_64 -o jniLibs build --offline) >build.log 2>&1; then
	tail -n 20 build.log
	echo "android-rust: cargo ndk build of the hello crate for arm64-v8a and x86_64 failed"
	exit 1
fi
for abi_machine in "arm64-v8a:ARM aarch64" "x86_64:x86-64"; do
	lib="hello/jniLibs/${abi_machine%%:*}/libhello.so" machine="${abi_machine#*:}"
	[[ -f "$lib" ]] || {
		echo "android-rust: cargo ndk did not produce $lib"
		exit 1
	}
	kind="$(file -b "$lib")"
	[[ "$kind" == *"ELF 64-bit"*"shared object"*"$machine"* ]] || {
		echo "android-rust: $lib is '$kind', expected an ELF 64-bit $machine shared object"
		exit 1
	}
done
echo "android-rust: cargo-ndk built a cdylib for arm64-v8a and x86_64, on rustc $(rustc --version | cut -d' ' -f2)"
