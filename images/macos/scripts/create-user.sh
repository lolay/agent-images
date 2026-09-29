#!/bin/bash
# Creates the single GUI user and installs its login profile, runner scripts
# (~/bin), Claude Code settings the runner seeds into sessions (~/.claude), and
# LaunchAgents (runner, git proxy watchdog). Runs in the guest as admin (passwordless sudo).
# Safe to re-run.
set -euo pipefail

: "${STAGING_DIR:?}" "${AGENT_USER:?}" "${USER_PASSWORD:?}"

readonly home="/Users/$AGENT_USER"

if ! id "$AGENT_USER" >/dev/null 2>&1; then
	sudo sysadminctl -addUser "$AGENT_USER" -fullName "Agent" -password "$USER_PASSWORD"
	sudo createhomedir -c -u "$AGENT_USER" >/dev/null
fi

sudo mkdir -p "$home/bin" "$home/.claude/hooks" "$home/workspace" "$home/Library/LaunchAgents" "$home/Library/Logs" \
	"$home/.config/agent-runner" "$home/.claude-runner"
sudo chmod 700 "$home/.claude-runner"

sudo install -m 644 "$STAGING_DIR/zprofile" "$home/.zprofile"
sudo install -m 755 "$STAGING_DIR/agent-runner.sh" "$home/bin/agent-runner"
sudo install -m 755 "$STAGING_DIR/agent-runner-watchdog.sh" "$home/bin/agent-runner-watchdog"
sudo install -m 755 "$STAGING_DIR/agent-ensure-simulator.sh" "$home/bin/agent-ensure-simulator"
for runner in "$STAGING_DIR"/runners/*.sh; do
	sudo install -m 755 "$runner" "$home/bin/agent-runner-$(basename "$runner" .sh)"
done

# The runner copies ~/.claude into each session's config at startup.
sudo install -m 644 "$STAGING_DIR/claude/settings.json" "$home/.claude/settings.json"
for hook in "$STAGING_DIR"/claude/hooks/*.sh; do
	sudo install -m 755 "$hook" "$home/.claude/hooks/$(basename "$hook")"
done

# Exact paths, not a glob under $home: after the chown below, ~agent/Library is
# owner-only, and this script (admin) can't list it to expand one.
for template in "$STAGING_DIR"/com.agent-images.*.plist; do
	plist="$home/Library/LaunchAgents/$(basename "$template")"
	sed "s#__AGENT_HOME__#$home#g" "$template" | sudo tee "$plist" >/dev/null
	sudo plutil -lint "$plist" >/dev/null
	sudo chmod 644 "$plist"
done

sudo chown -R "$AGENT_USER:staff" "$home"

# The agent user is root in its own VM: passwordless sudo, so sessions can run
# installers that need it, the host can set the hostname over tart exec (which
# runs as this user), and the runner can power the VM off after its session.
# Deliberate: the VM is thrown away after one session (specs/design.md §3).
# Validated before install: a broken sudoers.d file would break sudo for the
# rest of the build.
readonly sudoers="/etc/sudoers.d/agent"
sudoers_draft="$(mktemp)"
printf '%s ALL=(ALL) NOPASSWD: ALL\n' "$AGENT_USER" >"$sudoers_draft"
sudo visudo -cf "$sudoers_draft" >/dev/null
sudo install -m 440 -o root -g wheel "$sudoers_draft" "$sudoers"
rm -f "$sudoers_draft"

# A VM whose session locks or sleeps can't run UI tests.
# -H matters: macOS sudo keeps the caller's HOME by default.
sudo -u "$AGENT_USER" -H defaults -currentHost write com.apple.screensaver idleTime 0
