#!/bin/bash
# The rust toolset's toolchain: rustup only manages toolchains, so install one
# and sessions start with it. Run by install-toolset.sh after the Brewfile, with
# Homebrew on PATH.
set -euo pipefail

# The toolchain: a channel (stable), a minor line (1.91) or a version (1.91.0, a
# pin: gcp.md "Pinning a dependency"). The Cloud Run build passes the locked
# version (scripts/cloud/lock.sh); the VM images take this default.
toolchain="${RUST_TOOLCHAIN:-stable}"

# brew --prefix rustup would look the formula up in the API data, which
# install-toolset.sh has just cleaned up, and fetch it again (on stderr); opt/
# is the same path without the lookup.
PATH="$(brew --prefix)/opt/rustup/bin:$PATH"
# The minimal profile plus clippy and rustfmt is the default profile without
# rust-docs, the offline docs (about 900 MB). rustup reports progress on stderr,
# which a green build doesn't allow, so it goes to the log with the rest.
rustup toolchain install "$toolchain" --profile minimal --component clippy,rustfmt 2>&1
rustup default "$toolchain" 2>&1
printf '==> install-toolset: %s\n' "$(cargo --version)"
