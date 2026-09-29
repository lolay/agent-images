#!/bin/bash
# Creates the single user and installs its login profile, runner scripts
# (~/bin), Claude Code settings the runner seeds into sessions (~/.claude), and
# systemd units (runner, git proxy watchdog). Runs in the guest as root, first of
# the provisioning scripts. Safe to re-run.
#
# Management goes through `lxc exec` (lxd-agent), so the image has no SSH server
# and no other login user: the cloud image's default `ubuntu` user (passwordless
# sudo) is removed, and cloud-init is told not to recreate it in each clone.
set -euo pipefail
shopt -s nullglob

: "${STAGING_DIR:?}" "${AGENT_USER:?}"

readonly home="/home/$AGENT_USER"

log() { printf '==> create-user: %s\n' "$*"; }

if ! id "$AGENT_USER" >/dev/null 2>&1; then
	log "creating $AGENT_USER"
	useradd --create-home --shell /bin/bash --comment "Agent" "$AGENT_USER"
fi
# /dev/kvm is root:kvm 0660; the emulator needs it. systemd creates the group,
# so it's there in the VM image; create it if not.
getent group kvm >/dev/null || groupadd --system kvm
usermod -aG kvm "$AGENT_USER"

install -d -o "$AGENT_USER" -g "$AGENT_USER" -m 755 \
	"$home/bin" "$home/.claude" "$home/.claude/hooks" "$home/workspace" "$home/.config" \
	"$home/.config/agent-runner" "$home/.local" "$home/.local/state"
install -d -o "$AGENT_USER" -g "$AGENT_USER" -m 700 "$home/.claude-runner"

install -m 644 "$STAGING_DIR/profile.sh" /etc/profile.d/agent-images.sh

install -o "$AGENT_USER" -g "$AGENT_USER" -m 755 "$STAGING_DIR/agent-runner.sh" "$home/bin/agent-runner"
install -o "$AGENT_USER" -g "$AGENT_USER" -m 755 "$STAGING_DIR/agent-runner-watchdog.sh" "$home/bin/agent-runner-watchdog"
install -o "$AGENT_USER" -g "$AGENT_USER" -m 755 "$STAGING_DIR/agent-emulator.sh" "$home/bin/agent-emulator"
for runner in "$STAGING_DIR"/runners/*.sh; do
	install -o "$AGENT_USER" -g "$AGENT_USER" -m 755 "$runner" "$home/bin/agent-runner-$(basename "$runner" .sh)"
done

# The runner copies ~/.claude into each session's config at startup.
for file in "$STAGING_DIR"/claude/*.json "$STAGING_DIR"/claude/*.md; do
	install -o "$AGENT_USER" -g "$AGENT_USER" -m 644 "$file" "$home/.claude/$(basename "$file")"
done
for hook in "$STAGING_DIR"/claude/hooks/*.sh; do
	install -o "$AGENT_USER" -g "$AGENT_USER" -m 755 "$hook" "$home/.claude/hooks/$(basename "$hook")"
done

for template in "$STAGING_DIR"/agent-runner*.service "$STAGING_DIR"/agent-runner*.path "$STAGING_DIR"/agent-runner*.timer; do
	sed -e "s#__AGENT_HOME__#$home#g" -e "s#__AGENT_USER__#$AGENT_USER#g" "$template" \
		>"/etc/systemd/system/$(basename "$template")"
	chmod 644 "/etc/systemd/system/$(basename "$template")"
done
systemctl daemon-reload
# The runner starts when runner.env appears; the watchdog checks every 30 s.
systemctl enable agent-runner.path agent-runner-watchdog.timer

# Ephemeral runners power the VM off after one session. This is the only root
# command the agent user gets. Validated before install: a broken sudoers.d file
# would break sudo for the rest of the build.
readonly sudoers="/etc/sudoers.d/agent-shutdown"
sudoers_draft="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: /sbin/shutdown -h now\n' "$AGENT_USER" >"$sudoers_draft"
visudo -cf "$sudoers_draft" >/dev/null
install -m 440 -o root -g root "$sudoers_draft" "$sudoers"
rm -f "$sudoers_draft"

if id ubuntu >/dev/null 2>&1; then
	log "removing the cloud image's default user"
	userdel --remove ubuntu 2>/dev/null || userdel ubuntu
fi
rm -f /etc/sudoers.d/90-cloud-init-users
install -d /etc/cloud/cloud.cfg.d
cat >/etc/cloud/cloud.cfg.d/99-agent-images.cfg <<'EOF'
# agent-images: agent is the only user. Don't create the default user (it gets
# passwordless sudo) when cloud-init runs again in each clone.
users: []
disable_root: true
ssh_pwauth: false
EOF

if dpkg -s openssh-server >/dev/null 2>&1; then
	log "removing openssh-server (management is lxc exec)"
	DEBIAN_FRONTEND=noninteractive apt-get purge -y openssh-server
fi
log "done"
