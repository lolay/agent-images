# shellcheck shell=sh
# The rust toolset (images/toolsets/rust): what `cargo install` installs
# (cargo-ndk, on the Android image) goes to CARGO_HOME's bin, at the back of
# PATH. The cloud profile points CARGO_HOME at scratch; elsewhere it's cargo's
# default, ~/.cargo. rustup's proxies are at the front (rust.path, install.sh).
case ":$PATH:" in
*":${CARGO_HOME:-$HOME/.cargo}/bin:"*) ;;
*) PATH="$PATH:${CARGO_HOME:-$HOME/.cargo}/bin" ;;
esac
