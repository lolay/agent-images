# shellcheck shell=sh
# Installed as /etc/profile.d/agent-images.sh in the cloud images, and sourced by
# the entrypoint, so the runner, its sessions and any login shell share one
# environment. On Cloud Run everything outside the scratch volume is memory, so
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
# and rustc proxies. The Dockerfile sets the same PATH.
case ":$PATH:" in
*":$HOME/bin:"*) ;;
*) PATH="$HOME/bin:$HOME/.local/bin:/home/linuxbrew/.linuxbrew/opt/rustup/bin:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:$PATH" ;;
esac
case ":$PATH:" in
*":$CARGO_HOME/bin:"*) ;;
*) PATH="$PATH:$CARGO_HOME/bin" ;;
esac
export PATH
