#!/bin/bash
# The android-rust toolset: Rust's Android targets and cargo-ndk, which builds
# a crate with the NDK the android toolset installed and lays the libraries out
# as jniLibs. Needs the rust and android toolsets first. Run by
# install-toolset.sh, as the image's user.
set -euo pipefail

# The same rustup proxies the rust toolset's install.sh uses.
PATH="/home/linuxbrew/.linuxbrew/opt/rustup/bin:$HOME/.cargo/bin:$PATH"
# Phones (arm64-v8a, armeabi-v7a) and the emulator (x86_64); i686 is left out,
# as 32-bit x86 devices and emulators are gone. rustup reports on stderr, which
# a green build doesn't allow.
rustup target add aarch64-linux-android armv7-linux-androideabi x86_64-linux-android 2>&1
# Not in Homebrew; cargo builds it (about a minute) into ~/.cargo/bin.
cargo install --locked --quiet cargo-ndk 2>&1
printf '==> install-toolset: %s\n' "$(cargo ndk --version)"
