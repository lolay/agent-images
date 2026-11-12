# shellcheck shell=sh
# Installed as /etc/profile.d/agent-images.sh in the cloud images and sourced by
# the entrypoint, every login shell, ~/.bashrc and (through BASH_ENV) every
# non-interactive bash, so a session's shell has the same environment however it
# was started. Safe to source any number of times. On Cloud Run everything outside the scratch volume is memory, so
# temporary files and caches go to scratch.
export AGENT_SCRATCH="${AGENT_SCRATCH:-/scratch}"
export AGENT_DATASETS="${AGENT_DATASETS:-/mnt/datasets}"
export TMPDIR="$AGENT_SCRATCH/tmp"
export XDG_CACHE_HOME="$AGENT_SCRATCH/cache"
export PIP_CACHE_DIR="$XDG_CACHE_HOME/pip"
export npm_config_cache="$XDG_CACHE_HOME/npm"
export GOMODCACHE="$XDG_CACHE_HOME/go-mod"
export HOMEBREW_CACHE="$XDG_CACHE_HOME/Homebrew"
export HOMEBREW_TEMP="$TMPDIR"
export HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1
# Cargo's registry and `cargo install` land in CARGO_HOME, and pnpm's store
# outside its cache dir; the toolchains themselves are in the image.
export CARGO_HOME="$XDG_CACHE_HOME/cargo"
export pnpm_config_store_dir="$XDG_CACHE_HOME/pnpm-store"
# A repo's .terraform-version that isn't the image's Terraform downloads on use.
export TENV_AUTO_INSTALL=true
# Homebrew ahead of the system, as `brew shellenv` puts it, so python3 is
# Homebrew's current release rather than Ubuntu's; rustup's bin holds the cargo
# and rustc proxies. The Dockerfile sets the same PATH. HOMEBREW_PREFIX is
# brew shellenv's name for the prefix; the tests point it at a fake one.
HOMEBREW_PREFIX="${HOMEBREW_PREFIX:-/home/linuxbrew/.linuxbrew}"
export HOMEBREW_PREFIX
# PATH is set up once per environment, and then it's the caller's: a test that
# puts a fake gh or curl first and runs a bash script must reach the fake, and
# that bash re-sources this through BASH_ENV. AGENT_PROFILE_PATH_DONE, exported
# at the end, marks it. Login and interactive shells set it up again (a fresh
# shell gets the image's PATH and the gh shim in front), and so does a caller
# that unsets the marker first: the session-profile hook does, since Claude
# Code's shell snapshot puts back a PATH without the shim.
agent_set_path=
[ -n "${AGENT_PROFILE_PATH_DONE:-}" ] || agent_set_path=1
case "$-" in *i*) agent_set_path=1 ;; esac
case "$0" in -*) agent_set_path=1 ;; esac
# shellcheck disable=SC3044 # bash only, and guarded
if [ -n "${BASH_VERSION:-}" ] && shopt -q login_shell 2>/dev/null; then agent_set_path=1; fi
# What the image's toolsets recorded (images/toolsets/install-toolset.sh): their
# PATH entries go after ~/bin and before Homebrew (the gis toolset's Python
# ahead of any Homebrew one), and their profile fragments are sourced last.
export AGENT_TOOLSETS_DIR="${AGENT_TOOLSETS_DIR:-/etc/agent-images/toolsets}"
if [ -n "$agent_set_path" ]; then
	agent_toolset_path=""
	for agent_file in "$AGENT_TOOLSETS_DIR"/*.path; do
		[ -f "$agent_file" ] || continue
		while IFS= read -r agent_dir || [ -n "$agent_dir" ]; do
			case "$agent_dir" in "" | \#*) continue ;; esac
			agent_toolset_path="${agent_toolset_path:+$agent_toolset_path:}$agent_dir"
		done <"$agent_file"
	done
	# The front of PATH is always these, in this order, whatever PATH the shell
	# inherited (a session's can lack them, or have them behind /usr/bin); duplicates
	# go. A PATH that already starts with them is left alone.
	agent_path_front="$HOME/bin:$HOME/.local/bin${agent_toolset_path:+:$agent_toolset_path}:$HOMEBREW_PREFIX/opt/rustup/bin:$HOMEBREW_PREFIX/bin:$HOMEBREW_PREFIX/sbin"
	case "$PATH" in
	"$agent_path_front" | "$agent_path_front":*) ;;
	*)
		PATH="$agent_path_front:$(printf '%s\n' "$PATH" | awk -v front="$agent_path_front" '
			BEGIN { RS = ":"; n = split(front, dirs, ":"); for (i = 1; i <= n; i++) skip[dirs[i]] = 1 }
			{ sub(/\n$/, "") }
			$0 != "" && !($0 in skip) && !seen[$0]++ { out = out (out == "" ? "" : ":") $0 }
			END { printf "%s", out }')"
		;;
	esac
	unset agent_path_front agent_toolset_path agent_file agent_dir
	case ":$PATH:" in
	*":$CARGO_HOME/bin:"*) ;;
	*) PATH="$PATH:$CARGO_HOME/bin" ;;
	esac
	export PATH
fi
for agent_file in "$AGENT_TOOLSETS_DIR"/*.profile.sh; do
	# shellcheck source=/dev/null
	[ -f "$agent_file" ] && . "$agent_file"
done
unset agent_file
# Last, so nothing above moves it: Claude Code's gh shim goes in front of
# everything (images/shared/profile-gh-shim.sh).
if [ -n "$agent_set_path" ]; then
	agent_file="${AGENT_GH_SHIM_PROFILE:-/etc/agent-images/profile-gh-shim.sh}"
	# shellcheck source=/dev/null
	[ -f "$agent_file" ] && . "$agent_file"
	unset agent_file
fi
export AGENT_PROFILE_PATH_DONE=1
unset agent_set_path
