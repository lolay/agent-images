#!/bin/bash
# The rust toolset's stable toolchain: rustup only manages toolchains, so
# install the current one and sessions start with it. Run by install-toolset.sh
# after the Brewfile, with Homebrew on PATH.
set -euo pipefail

PATH="$(brew --prefix rustup)/bin:$PATH"
# The minimal profile plus clippy and rustfmt is the default profile without
# rust-docs, the offline docs (about 900 MB). rustup reports progress on stderr,
# which a green build doesn't allow, so it goes to the log with the rest.
rustup toolchain install stable --profile minimal --component clippy,rustfmt 2>&1
printf '==> install-toolset: %s\n' "$(cargo --version)"
