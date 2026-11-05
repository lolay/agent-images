#!/bin/bash
# Adds agent-images to the guest user: the cloud image's own user (ubuntu), which
# cloud-init creates with passwordless sudo, the Linux counterpart of the macOS
# image's admin. Installs the runner scripts (~/bin), the Claude Code settings the
# runner seeds into sessions (~/.claude), the environment login shells get
# (/etc/profile.d), and the runner and watchdog systemd units, and puts the user
# in kvm for the emulator. Runs in the guest as that user, first of the
# provisioning scripts. Safe to re-run.
set -euo pipefail
shopt -s nullglob

: "${STAGING_DIR:?}" "${GUEST_USER:?}"

[[ "$(id -un)" == "$GUEST_USER" ]] || {
	echo "expected to run as $GUEST_USER, not $(id -un)" >&2
	exit 1
}
# The runner powers the VM off with sudo, and sessions install packages with it.
# Fail the build if a new base image stops giving this user passwordless sudo.
sudo -n true 2>/dev/null || {
	echo "$GUEST_USER has no passwordless sudo; the base image's cloud-init should give it one" >&2
	exit 1
}
readonly home="$HOME"

# /dev/kvm is root:kvm 0660; the emulator needs it. systemd creates the group,
# so it's there in the VM image; create it if not.
getent group kvm >/dev/null || sudo groupadd --system kvm
sudo usermod -aG kvm "$GUEST_USER"

mkdir -p "$home/bin" "$home/.claude/hooks" "$home/workspace" "$home/.config/agent-runner" \
	"$home/.claude-runner" "$home/.local/state"
chmod 700 "$home/.claude-runner"

install -m 755 "$STAGING_DIR/agent-runner.sh" "$home/bin/agent-runner"
install -m 755 "$STAGING_DIR/agent-runner-watchdog.sh" "$home/bin/agent-runner-watchdog"
install -m 755 "$STAGING_DIR/agent-emulator.sh" "$home/bin/agent-emulator"
for runner in "$STAGING_DIR"/runners/*.sh; do
	install -m 755 "$runner" "$home/bin/agent-runner-$(basename "$runner" .sh)"
done

# The runner copies ~/.claude into each session's config at startup.
for file in "$STAGING_DIR"/claude/*.json "$STAGING_DIR"/claude/*.md; do
	install -m 644 "$file" "$home/.claude/$(basename "$file")"
done
for hook in "$STAGING_DIR"/claude/hooks/*.sh; do
	install -m 755 "$hook" "$home/.claude/hooks/$(basename "$hook")"
done

sudo install -m 644 "$STAGING_DIR/profile.sh" /etc/profile.d/agent-images.sh

for template in "$STAGING_DIR"/agent-runner*.service "$STAGING_DIR"/agent-runner*.path "$STAGING_DIR"/agent-runner*.timer; do
	sed -e "s#__GUEST_HOME__#$home#g" -e "s#__GUEST_USER__#$GUEST_USER#g" "$template" |
		sudo tee "/etc/systemd/system/$(basename "$template")" >/dev/null
done
sudo systemctl daemon-reload
# The runner starts when runner.env appears; the watchdog checks every 30 s.
sudo systemctl enable --quiet agent-runner.path agent-runner-watchdog.timer
