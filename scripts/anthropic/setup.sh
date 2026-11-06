#!/bin/bash
# Setup script for an Anthropic-hosted cloud environment that gives its
# sessions agent-linux's tools (specs/anthropic-cloud.md, "Matching agent-linux
# on Anthropic's cloud"). Paste into the environment's setup script as:
#
#   bash agent-images/scripts/anthropic/setup.sh
#
# It runs as root, one directory above the session's repositories, so
# agent-images must be one of them. It installs the same toolsets as
# images/cloud/linux/Dockerfile with the same installer, timing each into
# /var/log/agent-images-setup.tsv.
#
# Homebrew won't run as root outside a container it recognizes, and Anthropic's
# VM isn't one (no /.dockerenv), so Homebrew is owned by the stock image's
# ubuntu user, as in agent-linux. Root sessions get a brew wrapper that runs it
# as ubuntu.
set -euo pipefail

readonly toolsets=(core native jvm browser display node python go rust proto terraform)
readonly log=/var/log/agent-images-setup.tsv
readonly user=ubuntu
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly here

now() { date +%s.%N; }
mark() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >>"$log"; }

: >"$log"
mark setup start "$(now)"

# install-toolset.sh runs as a user with passwordless sudo.
id "$user" >/dev/null
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$user" >/etc/sudoers.d/agent-images
chmod 440 /etc/sudoers.d/agent-images

# A copy the user can read, wherever the checkout is.
work="$(mktemp -d)"
cp -r "$here/images/toolsets" "$work/"
chmod -R a+rX "$work"

# sudo drops the environment; the agent proxy needs these. Not its CA
# variables (SSL_CERT_FILE and the rest): they name /root/.ccr, which the user
# can't read, and the system store already carries the proxy's CA.
pass=()
for name in HTTPS_PROXY https_proxy HTTP_PROXY http_proxy NO_PROXY no_proxy; do
	[[ -z "${!name:-}" ]] || pass+=("$name=${!name}")
done

for toolset in "${toolsets[@]}"; do
	start="$(now)"
	# APT_DROP_LISTS stays off: sessions here are root and can apt-get.
	if sudo -u "$user" -H env "${pass[@]}" APT_DROP_LISTS=false \
		bash "$work/toolsets/install-toolset.sh" "$toolset"; then
		mark "$toolset" ok "$(awk -v a="$(now)" -v b="$start" 'BEGIN { printf "%.1f", a - b }')"
	else
		mark "$toolset" failed "$(awk -v a="$(now)" -v b="$start" 'BEGIN { printf "%.1f", a - b }')"
		exit 1
	fi
done
rm -rf "$work"

# Root sessions: Homebrew as its owner, and agent-linux's PATH order (toolset
# paths and Homebrew ahead of Anthropic's runtimes).
install -d -m 755 /opt/agent-images/bin
cat >/opt/agent-images/bin/brew <<EOF
#!/bin/sh
exec sudo -u $user -H /home/linuxbrew/.linuxbrew/bin/brew "\$@"
EOF
chmod 755 /opt/agent-images/bin/brew
# The toolchains the user installed are the ones root's sessions use:
# Anthropic sets RUSTUP_HOME=/root/.rustup, which would send Homebrew's rustup
# to root's older toolchain, and tenv keeps its selected Terraform per user.
cat >/etc/profile.d/zz-agent-images.sh <<EOF
# agent-images toolsets (scripts/anthropic/setup.sh): Homebrew's tools ahead of
# the image's own runtimes, as in agent-linux. Sourced by login shells, root's
# ~/.bashrc and, through a SessionStart hook, every Bash tool command.
export HOMEBREW_PREFIX=/home/linuxbrew/.linuxbrew HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1
export RUSTUP_HOME=/home/$user/.rustup TENV_ROOT=/home/$user/.tenv TENV_AUTO_INSTALL=true
case ":\$PATH:" in
*":/opt/agent-images/bin:"*) ;;
*) export PATH="/opt/agent-images/bin:\$HOMEBREW_PREFIX/opt/rustup/bin:\$HOMEBREW_PREFIX/bin:\$HOMEBREW_PREFIX/sbin:\$PATH" ;;
esac

# The display toolset: agent-linux's entrypoint starts Xvfb at :99 before the
# session; here there's no entrypoint, so the first shell starts it (detached,
# so it outlives that shell) and every shell points DISPLAY at it.
if [ -z "\${DISPLAY:-}" ] && command -v Xvfb >/dev/null 2>&1; then
	export DISPLAY=:99
	if [ ! -S /tmp/.X11-unix/X99 ]; then
		(setsid Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &)
		for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
			[ -S /tmp/.X11-unix/X99 ] && break
			sleep 0.1
		done
	fi
fi
EOF
# Interactive shells that aren't login shells read ~/.bashrc only.
grep -qF zz-agent-images.sh /root/.bashrc ||
	printf '\n. /etc/profile.d/zz-agent-images.sh\n' >>/root/.bashrc

# Claude Code's Bash tool reads neither: its shell snapshot ends by exporting
# Claude Code's own PATH. A SessionStart hook can add to a session's
# environment through CLAUDE_ENV_FILE, which every Bash tool command sources, so
# root's user settings get one that sources the profile fragment. Anthropic's
# own hooks are in launcher-settings.json, not here.
install -d -m 755 /opt/agent-images/libexec

# Anthropic rebuilds its environment cache only when the environment's own
# setup script changes, not when this repo's does, so a session can start from
# a build of an older setup.sh or older toolsets. The build records a
# fingerprint of both, from git's blob hashes, and the hook compares it with the
# session's fresh checkout.
cat >/opt/agent-images/libexec/build-fingerprint <<'EOF'
#!/bin/sh
# Prints the fingerprint of an agent-images checkout's setup inputs.
git -C "$1" ls-files -s scripts/anthropic/setup.sh images/toolsets 2>/dev/null |
	sha256sum | cut -d' ' -f1
EOF
chmod 755 /opt/agent-images/libexec/build-fingerprint
printf '%s %s\n' "$(/opt/agent-images/libexec/build-fingerprint "$here")" "$here" \
	>/opt/agent-images/build-fingerprint

cat >/opt/agent-images/libexec/session-env <<'EOF'
#!/bin/sh
# SessionStart hook (scripts/anthropic/setup.sh): every Bash tool command
# sources the agent-images profile fragment, and the session hears when its
# environment was built from an older setup.sh or older toolsets than its
# checkout's.
cat >/dev/null
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
	grep -qF zz-agent-images.sh "$CLAUDE_ENV_FILE" 2>/dev/null ||
		printf '. /etc/profile.d/zz-agent-images.sh\n' >>"$CLAUDE_ENV_FILE"
fi
[ -r /opt/agent-images/build-fingerprint ] || exit 0
read -r built checkout </opt/agent-images/build-fingerprint || exit 0
[ -d "$checkout/.git" ] || exit 0
now="$(/opt/agent-images/libexec/build-fingerprint "$checkout")"
[ -n "$now" ] && [ "$now" != "$built" ] || exit 0
message="agent-images: this environment was built from an older scripts/anthropic/setup.sh or images/toolsets than this checkout's, and Anthropic's cache won't rebuild on its own. To update it, edit the environment's setup script (a comment line is enough); the next session reruns it."
printf '{"systemMessage":"%s","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s Tell the user this before starting work."}}\n' "$message" "$message"
EOF
chmod 755 /opt/agent-images/libexec/session-env
settings=/root/.claude/settings.json
install -d -m 755 /root/.claude
[[ -s "$settings" ]] || printf '{}\n' >"$settings"
jq '.hooks.SessionStart = ((.hooks.SessionStart // [])
	| map(select(any(.hooks[]?; .command == "/opt/agent-images/libexec/session-env") | not))
	+ [{hooks: [{type: "command", command: "/opt/agent-images/libexec/session-env"}]}])' \
	"$settings" >"$settings.new"
mv "$settings.new" "$settings"

# SessionStart hooks run with Claude Code's own PATH: CLAUDE_ENV_FILE reaches
# only Bash tool commands, so a hook running a repo's `make doctor` wouldn't
# find Homebrew's tools. The ones Anthropic's image lacks altogether go in
# /usr/local/bin, which is on that PATH; the runtimes it has (Node, Go, Python,
# Rust) stay its own there. Nothing already in /usr/local/bin is replaced.
readonly brew_bin=/home/linuxbrew/.linuxbrew/bin
for tool in triage nowline sops shellcheck actionlint pre-commit protoc buf grpcurl; do
	[[ -e "/usr/local/bin/$tool" || ! -x "$brew_bin/$tool" ]] || ln -s "$brew_bin/$tool" "/usr/local/bin/$tool"
done
# tenv finds the selected Terraform through TENV_ROOT, which hooks don't get.
if [[ ! -e /usr/local/bin/terraform && -x "$brew_bin/terraform" ]]; then
	cat >/usr/local/bin/terraform <<EOF
#!/bin/sh
TENV_ROOT="\${TENV_ROOT:-/home/$user/.tenv}" exec $brew_bin/terraform "\$@"
EOF
	chmod 755 /usr/local/bin/terraform
fi

mark setup end "$(now)"
