#!/bin/bash
# Adds agent-images to the guest user: the base image's admin, which already
# logs in automatically, owns Homebrew, and has passwordless sudo. Installs the
# runner scripts (~/bin), a PATH block in ~/.zprofile, the Claude Code settings
# the runner seeds into sessions (~/.claude), and the runner and watchdog
# LaunchAgents; keeps the VM awake. Runs in the guest as that user over Packer's
# SSH. Safe to re-run.
set -euo pipefail

: "${STAGING_DIR:?}" "${GUEST_USER:?}"

[[ "$(id -un)" == "$GUEST_USER" ]] || {
	echo "expected to run as $GUEST_USER, not $(id -un)" >&2
	exit 1
}
readonly home="$HOME"

# Simulator and UI tests need a live GUI session, so the base image's automatic
# login must still land on this user. Fail the build if a new base image drops it.
auto_login_user="$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null || true)"
[[ "$auto_login_user" == "$GUEST_USER" ]] || {
	echo "the base image logs in automatically as '${auto_login_user:-nobody}', not $GUEST_USER" >&2
	exit 1
}
if sudo fdesetup status | grep -q 'FileVault is On'; then
	echo "FileVault is on; automatic login will not work" >&2
	exit 1
fi

mkdir -p "$home/bin" "$home/.claude/hooks" "$home/workspace" "$home/Library/LaunchAgents" \
	"$home/Library/Logs" "$home/.config/agent-runner" "$home/.claude-runner"
chmod 700 "$home/.claude-runner"

install -m 755 "$STAGING_DIR/agent-runner.sh" "$home/bin/agent-runner"
install -m 755 "$STAGING_DIR/agent-runner-watchdog.sh" "$home/bin/agent-runner-watchdog"
install -m 755 "$STAGING_DIR/agent-ensure-simulator.sh" "$home/bin/agent-ensure-simulator"
for runner in "$STAGING_DIR"/runners/*.sh; do
	install -m 755 "$runner" "$home/bin/agent-runner-$(basename "$runner" .sh)"
done

# The base image's ~/.zprofile sets up more than Homebrew, so add a marked block
# rather than replace the file; a re-run swaps the block.
readonly zprofile="$home/.zprofile"
touch "$zprofile"
sed -i '' '/^# >>> agent-images >>>$/,/^# <<< agent-images <<<$/d' "$zprofile"
cat "$STAGING_DIR/zprofile" >>"$zprofile"

# The runner copies ~/.claude into each session's config at startup.
install -m 644 "$STAGING_DIR/claude/settings.json" "$home/.claude/settings.json"
for hook in "$STAGING_DIR"/claude/hooks/*.sh; do
	install -m 755 "$hook" "$home/.claude/hooks/$(basename "$hook")"
done

for template in "$STAGING_DIR"/com.agent-images.*.plist; do
	plist="$home/Library/LaunchAgents/$(basename "$template")"
	sed "s#__GUEST_HOME__#$home#g" "$template" >"$plist"
	plutil -lint "$plist" >/dev/null
	chmod 644 "$plist"
done

# A VM whose session locks or sleeps can't run UI tests.
defaults -currentHost write com.apple.screensaver idleTime 0
sudo pmset -a sleep 0 displaysleep 0 disksleep 0
