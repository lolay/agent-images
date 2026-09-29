#!/bin/bash
# Installs, removes, or reports a host LaunchAgent that keeps one ephemeral
# runner loop (runner-run.sh) going across logouts and reboots.
#
# Usage: scripts/runner-service.sh install|uninstall|status <vm>
#
# The LaunchAgent starts at the host user's login. Turn on automatic login for
# that user so runners come back after a host restart.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

readonly action="${1:?usage: runner-service.sh install|uninstall|status <vm>}"
readonly vm="${2:?usage: runner-service.sh install|uninstall|status <vm>}"
repo_dir="$(cd "$script_dir/.." && pwd)"
readonly repo_dir
readonly label="com.agent-images.$vm"
readonly plist="$HOME/Library/LaunchAgents/$label.plist"
readonly log_file="$repo_dir/build/logs/$vm.runner.log"
domain="gui/$(id -u)"
readonly domain

is_loaded() { launchctl print "$domain/$label" >/dev/null 2>&1; }

write_plist() {
	# ExitTimeOut leaves time for the loop to stop and delete its VM.
	cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$repo_dir/scripts/runner-run.sh</string>
    <string>$vm</string>
  </array>
  <key>WorkingDirectory</key><string>$repo_dir</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ExitTimeOut</key><integer>120</integer>
  <key>StandardOutPath</key><string>$log_file</string>
  <key>StandardErrorPath</key><string>$log_file</string>
</dict>
</plist>
EOF
	plutil -lint "$plist" >/dev/null
}

case "$action" in
install)
	[[ -f "$repo_dir/vms/$vm.env" ]] || die "no vms/$vm.env; copy one of vms/example-*.env"
	is_loaded && die "$label is already installed; uninstall it first"
	mkdir -p "$(dirname "$plist")" "$(dirname "$log_file")"
	write_plist
	launchctl bootstrap "$domain" "$plist"
	log "$vm: installed $label (log: $log_file)"
	;;
uninstall)
	if is_loaded; then
		# bootout sends SIGTERM; the loop deletes its VM before exiting.
		launchctl bootout "$domain/$label"
	fi
	rm -f "$plist"
	log "$vm: uninstalled $label"
	;;
status)
	if is_loaded; then
		state="$(launchctl print "$domain/$label" | sed -n -E 's/^[[:space:]]*state = (.*)$/\1/p' | head -n 1)"
		printf '%s\n  service  %s (%s)\n' "$vm" "$label" "${state:-loaded}"
	else
		printf '%s\n  service  not installed\n' "$vm"
	fi
	if [[ -f "$log_file" ]]; then
		printf '  loop log\n'
		tail -n "${LOG_LINES:-5}" "$log_file" | sed 's/^/    /'
	fi
	;;
*)
	die "unknown action $action (install, uninstall, or status)"
	;;
esac
