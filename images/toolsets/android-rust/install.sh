#!/bin/bash
# The android-rust toolset: Rust's Android targets and cargo-ndk, which builds
# a crate with the NDK the android toolset installed and lays the libraries out
# as jniLibs. Needs the rust and android toolsets first. Run by
# install-toolset.sh, as the image's user.
set -euo pipefail

# The rust toolset's rustup proxies and cargo's install bin, as its record has
# them, and the android toolset's NDK, which cargo-ndk builds against.
toolset_env rust android
# Phones (arm64-v8a, armeabi-v7a) and the emulator (x86_64); i686 is left out,
# as 32-bit x86 devices and emulators are gone. rustup reports on stderr, which
# a green build doesn't allow.
rustup target add aarch64-linux-android armv7-linux-androideabi x86_64-linux-android 2>&1
# Not in Homebrew; cargo builds it (about a minute) into ~/.cargo/bin, which the
# rust toolset's profile puts on every session's PATH.
cargo install --locked --quiet cargo-ndk 2>&1
log "$(cargo ndk --version)"
