#!/bin/bash
# Installs packages.txt from Ubuntu's archive, Homebrew and the Brewfile, the
# toolchains rustup and tenv manage, and tflint, then Claude Code with its native
# installer
# (~/.local/bin/claude, which `claude update` upgrades; the runner does that at
# every start). Runs in the guest as the guest user, after setup-user.sh.
# INSTALL_CLAUDE=false skips Claude Code: the cloud images install it in a later
# layer of their own (images/cloud/base/Dockerfile), since it changes most often.
#
# No apt-get upgrade: each build starts from a fresh ubuntu:24.04, the way the
# macOS image doesn't run softwareupdate. needrestart stays out of the build.
set -euo pipefail

: "${STAGING_DIR:?}"
: "${INSTALL_CLAUDE:=true}"

log() { printf '==> install-packages: %s\n' "$*"; }
apt() { sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get -q "$@"; }

mapfile -t packages < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$STAGING_DIR/packages.txt")
log "installing ${#packages[@]} packages"
apt update
apt install -y --no-install-recommends "${packages[@]}"
apt clean

# Homebrew, for the Brewfile's toolchains and lolay/tap formulae. It installs to
# /home/linuxbrew/.linuxbrew and must not run as root; the guest user has sudo.
readonly brewfile="$STAGING_DIR/Brewfile"
readonly brew=/home/linuxbrew/.linuxbrew/bin/brew
if [[ ! -x "$brew" ]]; then
	log "installing Homebrew"
	NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
eval "$("$brew" shellenv)"
export HOMEBREW_NO_ENV_HINTS=1 HOMEBREW_NO_ANALYTICS=1

# Homebrew refuses to load formulae from untrusted third-party taps, so trust
# each Brewfile tap before the bundle loads them.
sed -n 's/^tap "\([^"]*\)".*/\1/p' "$brewfile" | while read -r tap; do
	brew tap "$tap"
	brew trust --tap "$tap"
done
log "installing the Brewfile"
brew bundle --file="$brewfile"

# rustup and tenv only manage toolchains; install the current ones so sessions
# start with them. Both report progress on stderr, which a green build doesn't
# allow, so it goes to the log with the rest.
PATH="$(brew --prefix rustup)/bin:$PATH"
log "installing the Rust stable toolchain"
rustup toolchain install stable --profile default 2>&1
log "installing the latest Terraform"
tenv tf install latest 2>&1
tenv tf use latest 2>&1

# tflint, from its release (Homebrew dropped it after its license change),
# checked against the release's checksums.
log "installing tflint"
readonly tflint_release=https://github.com/terraform-linters/tflint/releases/latest/download
tflint_tmp="$(mktemp -d)"
curl -fsSL -o "$tflint_tmp/tflint_linux_amd64.zip" "$tflint_release/tflint_linux_amd64.zip"
curl -fsSL -o "$tflint_tmp/checksums.txt" "$tflint_release/checksums.txt"
(cd "$tflint_tmp" && sha256sum --check --ignore-missing --quiet checksums.txt)
unzip -q -o "$tflint_tmp/tflint_linux_amd64.zip" tflint -d "$tflint_tmp"
sudo install -m 755 "$tflint_tmp/tflint" /usr/local/bin/tflint
rm -rf "$tflint_tmp"

log "$(brew list --versions nowline triage | tr '\n' ';')"
log "node $(node --version), pnpm $(pnpm --version), $(python3 --version), $(uv --version)"
log "$(go version), $(cargo --version), $(protoc --version), buf $(buf --version)"
log "$(CHECKPOINT_DISABLE=1 terraform version | head -n 1), $(tflint --version | head -n 1)"

[[ "$INSTALL_CLAUDE" == "true" ]] || exit 0
readonly claude="$HOME/.local/bin/claude"
if [[ ! -x "$claude" ]]; then
	log "installing Claude Code"
	curl -fsSL https://claude.ai/install.sh | bash
fi
log "claude $("$claude" --version)"
