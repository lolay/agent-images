#!/bin/bash
# Pre-approves the macOS privacy permissions (TCC) that session commands need
# for UI testing: Screen Recording, Accessibility, synthetic input, Input
# Monitoring, and Automation of common apps. Nobody is at a runner VM's screen,
# so an unanswered consent dialog blocks the command that asked and every later
# request behind it.
#
# Deliberate (specs/design.md §3): any code a session runs can record the VM's
# screen and drive its UI. The VM serves one session, is thrown away after it,
# and its user already has passwordless sudo.
#
# macOS holds the program a LaunchAgent starts responsible for everything under
# it, and the runner LaunchAgent starts /bin/zsh, so every command a session
# runs is checked as /bin/zsh (recorded by path, client type 1). Commands over
# `tart exec` are checked as the Tart guest agent, which the base image already
# grants. Automation is granted per target app: a target not listed here still
# prompts, so add it to automation_targets.
#
# Writes both TCC databases the way the Cirrus base image does (sqlite3 over
# Packer's SSH session). Runs in the guest as the guest user (passwordless sudo),
# who is logged in, so the per-user tccd is running.
set -euo pipefail

readonly responsible="/bin/zsh"
readonly services=(
	kTCCServiceScreenCapture # Screen Recording
	kTCCServiceAccessibility # control the computer, read UI (menu bar, Dock)
	kTCCServicePostEvent     # synthetic keyboard and mouse events
	kTCCServiceListenEvent   # Input Monitoring
)
readonly automation_targets=(
	com.apple.systemevents # UI scripting: menu bar, Dock, app windows
	com.apple.dock
	com.apple.iphonesimulator
	com.apple.dt.Xcode
	com.apple.finder
	com.apple.Safari
	com.apple.Terminal
)

log() { printf '==> grant-privacy: %s\n' "$*"; }

# macOS 27 moved the per-user database into a ProtectedSystem container; find it
# through the files the user's tccd has open, as the Cirrus base image does.
user_tcc_database() {
	local major
	major="$(sw_vers -productVersion | cut -d. -f1)"
	if ((major < 27)); then
		printf '%s\n' "$HOME/Library/Application Support/com.apple.TCC/TCC.db"
		return
	fi
	sudo lsof -a -u "$(id -u)" -c tccd -Fn |
		sed -n 's|^n\(/private/var/containers/Data/ProtectedSystem/.*/Data/Library/Application Support/com.apple.TCC/TCC.db\)$|\1|p' |
		sort -u
}

rows=()
for service in "${services[@]}"; do
	rows+=("('$service', 1, '$responsible', 2, 0, 1, NULL, 'UNUSED')")
done
for target in "${automation_targets[@]}"; do
	rows+=("('kTCCServiceAppleEvents', 1, '$responsible', 2, 0, 1, 0, '$target')")
done
values="$(
	IFS=,
	printf '%s' "${rows[*]}"
)"
readonly sql="INSERT OR REPLACE INTO access (service, client_type, client, auth_value,
	auth_reason, auth_version, indirect_object_identifier_type, indirect_object_identifier)
	VALUES $values;"

user_db="$(user_tcc_database)"
[[ -n "$user_db" && "$(printf '%s\n' "$user_db" | wc -l | tr -d ' ')" == 1 ]] || {
	echo "expected exactly one user TCC database, found: ${user_db:-none}" >&2
	exit 1
}
sudo test -f "$user_db" || {
	echo "user TCC database does not exist: $user_db" >&2
	exit 1
}

for db in "/Library/Application Support/com.apple.TCC/TCC.db" "$user_db"; do
	sudo sqlite3 "$db" "$sql"
	count="$(sudo sqlite3 "$db" "SELECT count(*) FROM access WHERE client = '$responsible' AND client_type = 1 AND auth_value = 2;")"
	((count >= ${#rows[@]})) || {
		echo "$db: expected ${#rows[@]} grants for $responsible, found $count" >&2
		exit 1
	}
	log "$db: $count grants for $responsible"
done

# Screen Recording has a second, separate prompt: "... is requesting to bypass
# the system private window picker and directly access your screen and audio."
# replayd shows it on a program's first direct capture and again every 30 days,
# tracked per program path (the responsible one, /bin/zsh) in this plist. It
# doesn't block the capture, but it stays on screen and lands in every later
# screenshot. Setting the next alert date far out means it never shows.
readonly capture_approvals="$HOME/Library/Group Containers/group.com.apple.replayd/ScreenCaptureApprovals.plist"
tart_guest_agent="$(realpath /opt/homebrew/bin/tart-guest-agent 2>/dev/null || true)"
python3 - "$capture_approvals" "$responsible" ${tart_guest_agent:+"$tart_guest_agent"} <<'PY'
import datetime
import os
import plistlib
import sys

path, programs = sys.argv[1], sys.argv[2:]
approvals = {}
if os.path.exists(path):
    with open(path, "rb") as f:
        approvals = plistlib.load(f)

now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)
never = datetime.datetime(2099, 1, 1)
for program in programs:
    entry = approvals.get(program, {})
    entry.update({
        "kScreenCaptureAlertableUsageCount": max(1, entry.get("kScreenCaptureAlertableUsageCount", 0)),
        "kScreenCaptureApprovalLastAlerted": now,
        "kScreenCaptureApprovalLastUsed": now,
        "kScreenCapturePrivacyHintDate": never,
        "kScreenCapturePrivacyHintPolicy": 2592000,
    })
    approvals[program] = entry

os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
with open(path, "wb") as f:
    plistlib.dump(approvals, f, fmt=plistlib.FMT_BINARY)
print(f"==> grant-privacy: {path}: no screen-capture alert before 2099 for {', '.join(programs)}")
PY
