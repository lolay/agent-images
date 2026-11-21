# shellcheck shell=sh
# The rust toolset (images/toolsets/rust): what `cargo install` installs
# (cargo-ndk, on the Android image) goes to the bin of CARGO_INSTALL_ROOT, or of
# CARGO_HOME without it, at the back of PATH. The cloud profile points
# CARGO_HOME at scratch; elsewhere it's cargo's default, ~/.cargo. rustup's
# proxies are at the front (rust.path, install.sh). Only while PATH is being set
# up (agent_set_path, toolset-profile.sh). A cargo config's install.root isn't
# read: that would take a subprocess in every shell.
# shellcheck disable=SC2154 # toolset-profile.sh's
if [ -n "${agent_set_path:-}" ]; then
	agent_cargo_bin="${CARGO_INSTALL_ROOT:-${CARGO_HOME:-$HOME/.cargo}}/bin"
	case ":$PATH:" in
	*":$agent_cargo_bin:"*) ;;
	*) PATH="$PATH:$agent_cargo_bin" ;;
	esac
	unset agent_cargo_bin
fi
