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
case ":$PATH:" in
*":$HOME/bin:"*) ;;
*) PATH="$HOME/bin:$HOME/.local/bin:$PATH:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin" ;;
esac
export PATH
