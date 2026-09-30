#!/bin/bash
# Removes the login-items announcements stored during the build ("Multiple
# Extensions Added", "zsh can run in the background"). macOS posts them as
# alerts, which stay on screen until someone dismisses them, and Notification
# Center shows stored ones again at every login; so every VM cloned from this
# image would start with them over its UI and in every UI-test screenshot.
# App notifications are untouched: only this one sender's records go.
#
# Silencing the sender doesn't work on macOS 27: it ignores the per-app
# notification settings and resets them when it posts. A session that adds a
# new background item still gets one live announcement.
#
# Runs last, after anything in the build that could post one.
set -euo pipefail

readonly db="$HOME/Library/Group Containers/group.com.apple.usernoted/db2/db"
readonly sender="com.apple.btmnotificationagent"

log() { printf '==> clear-notifications: %s\n' "$*"; }

if ! sudo test -f "$db"; then
	log "no Notification Center database; nothing to clear"
	exit 0
fi

readonly sender_app="SELECT app_id FROM app WHERE identifier = '$sender'"
for table in delivered displayed requests record; do
	sudo sqlite3 "$db" "DELETE FROM $table WHERE app_id IN ($sender_app);"
done
# Restart them so nothing held in memory shows or writes the old records back.
killall usernoted NotificationCenter 2>/dev/null || true
sleep 5

remaining="$(sudo sqlite3 "$db" "SELECT count(*) FROM record WHERE app_id IN ($sender_app);")"
((remaining == 0)) || {
	echo "$remaining $sender notifications are still stored in $db" >&2
	exit 1
}
log "no stored $sender notifications"
