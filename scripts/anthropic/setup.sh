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
cat >/etc/profile.d/zz-agent-images.sh <<'EOF'
# agent-images toolsets (scripts/anthropic/setup.sh): Homebrew's tools ahead of
# the image's own runtimes, as in agent-linux.
export HOMEBREW_PREFIX=/home/linuxbrew/.linuxbrew HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1
export PATH="/opt/agent-images/bin:$HOMEBREW_PREFIX/opt/rustup/bin:$HOMEBREW_PREFIX/bin:$HOMEBREW_PREFIX/sbin:$PATH"

# The display toolset: agent-linux's entrypoint starts Xvfb at :99 before the
# session; here there's no entrypoint, so the first shell starts it (detached,
# so it outlives that shell) and every shell points DISPLAY at it.
if [ -z "${DISPLAY:-}" ] && command -v Xvfb >/dev/null 2>&1; then
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
# Non-login shells read ~/.bashrc only.
grep -qF zz-agent-images.sh /root/.bashrc ||
	printf '\n. /etc/profile.d/zz-agent-images.sh\n' >>/root/.bashrc

mark setup end "$(now)"
