#!/bin/bash
# Creates the single GUI user and installs its login profile, runner scripts
# (~/bin), and LaunchAgent. Runs in the guest as admin (passwordless sudo).
# Safe to re-run.
set -euo pipefail

: "${STAGING_DIR:?}" "${AGENT_USER:?}" "${USER_PASSWORD:?}"

readonly home="/Users/$AGENT_USER"
readonly label="com.agent-images.runner"

if ! id "$AGENT_USER" >/dev/null 2>&1; then
	sudo sysadminctl -addUser "$AGENT_USER" -fullName "Agent" -password "$USER_PASSWORD"
	sudo createhomedir -c -u "$AGENT_USER" >/dev/null
fi

sudo mkdir -p "$home/bin" "$home/workspace" "$home/Library/LaunchAgents" "$home/Library/Logs" \
	"$home/.config/agent-runner/secrets" "$home/.claude-runner"
sudo chmod 700 "$home/.config/agent-runner/secrets" "$home/.claude-runner"

sudo install -m 644 "$STAGING_DIR/zprofile" "$home/.zprofile"
sudo install -m 755 "$STAGING_DIR/agent-runner.sh" "$home/bin/agent-runner"
for runner in "$STAGING_DIR"/runners/*.sh; do
	sudo install -m 755 "$runner" "$home/bin/agent-runner-$(basename "$runner" .sh)"
done

readonly plist="$home/Library/LaunchAgents/$label.plist"
sed "s#__AGENT_HOME__#$home#g" "$STAGING_DIR/$label.plist" | sudo tee "$plist" >/dev/null
sudo plutil -lint "$plist" >/dev/null

sudo chown -R "$AGENT_USER:staff" "$home"
sudo chmod 644 "$plist"

# Ephemeral runners power the VM off after one session. This is the only root
# command the agent user gets. Validated before install: a broken sudoers.d file
# would break sudo for the rest of the build.
readonly sudoers="/etc/sudoers.d/agent-shutdown"
sudoers_draft="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: /sbin/shutdown -h now\n' "$AGENT_USER" >"$sudoers_draft"
sudo visudo -cf "$sudoers_draft" >/dev/null
sudo install -m 440 -o root -g wheel "$sudoers_draft" "$sudoers"
rm -f "$sudoers_draft"

# A VM whose session locks or sleeps can't run UI tests.
# -H matters: macOS sudo keeps the caller's HOME by default.
sudo -u "$AGENT_USER" -H defaults -currentHost write com.apple.screensaver idleTime 0
