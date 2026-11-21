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
# The rest is the toolsets': PATH (with Homebrew ahead of the system, and
# CARGO_HOME's bin at the back through the rust toolset's profile), what they
# recorded, and Claude Code's gh shim in front (images/toolsets/toolset-profile.sh).
# The Dockerfile's ENV PATH has the fixed part (~/bin, Homebrew) and rustup's
# proxies, for what doesn't read this profile.
agent_toolset_profile="${AGENT_TOOLSET_PROFILE:-/etc/agent-images/toolset-profile.sh}"
# shellcheck source=images/toolsets/toolset-profile.sh
[ -f "$agent_toolset_profile" ] && . "$agent_toolset_profile"
unset agent_toolset_profile
