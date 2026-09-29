#!/bin/bash
# Shared helpers for host-side scripts. Sourced, not executed.
# Needs only tart and the macOS `security` tool, so it runs on any run host.

AGENT_USER="${AGENT_USER:-agent}"
readonly KEYCHAIN_SERVICE_PREFIX="agent-images"

die() {
	printf 'error: %s\n' "$*" >&2
	exit 1
}

log() { printf '==> %s\n' "$*"; }

# Waits until the guest agent answers, so tart exec works.
wait_for_guest() {
	local target_vm="$1" timeout="${2:-180}" waited=0
	until tart exec "$target_vm" true >/dev/null 2>&1; do
		((waited >= timeout)) && die "$target_vm: guest agent not reachable after ${timeout}s (is the VM running?)"
		sleep 5
		waited=$((waited + 5))
	done
}

# Prints a secret from the host Keychain: the VM-specific item if present,
# else the "default" one. Returns non-zero if neither exists.
keychain_secret() {
	local service="$KEYCHAIN_SERVICE_PREFIX.$1" target_vm="$2"
	security find-generic-password -s "$service" -a "$target_vm" -w 2>/dev/null ||
		security find-generic-password -s "$service" -a default -w 2>/dev/null
}

# Writes stdin to a path under the agent user's home, owner-only. The content
# travels over stdin so secrets never appear in a process list.
guest_write() {
	local target_vm="$1" relative_path="$2"
	# shellcheck disable=SC2016 # expanded by the guest shell, not here
	tart exec -i "$target_vm" sudo -u "$AGENT_USER" -H /bin/bash -c \
		'umask 077; target="$HOME/$1"; mkdir -p "$(dirname "$target")"; cat >"$target"' \
		_ "$relative_path"
}

# Reads KEY=value from a vms/<vm>.env file without executing it.
env_value() {
	local file="$1" key="$2"
	sed -n -E "s/^[[:space:]]*$key=(.*)$/\\1/p" "$file" | tail -n 1
}
