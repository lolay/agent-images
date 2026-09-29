#!/bin/bash
# Makes sure this user has an available iPhone simulator, creating one for the
# newest iOS runtime if not. Simulator devices are per user, while the runtimes
# are machine-wide (ensure-xcode.sh).
# Idempotent. Installed as ~/bin/agent-ensure-simulator; agent-runner-claude
# runs it at every start, and the image build tries it once.
set -euo pipefail

log() { printf '%s agent-ensure-simulator: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

if xcrun simctl list devices available | grep -q 'iPhone'; then
	log "iPhone simulator available"
	exit 0
fi

# The newest available iOS runtime (by version number, not list order) and, among
# the iPhones it supports, the newest model: the highest minRuntimeVersion, the
# iOS it first shipped with. Choosing from supportedDeviceTypes keeps the pair valid.
pair="$(xcrun simctl list -j | jq -r '
	(.devicetypes | map({key: .identifier, value: (.minRuntimeVersion // 0)}) | from_entries) as $first_ios
	| [.runtimes[] | select(.isAvailable and .platform == "iOS")]
	| max_by(.version | split(".") | map(tonumber? // 0)) // empty
	| [.identifier,
		([.supportedDeviceTypes[]? | select(.productFamily == "iPhone")]
			| max_by($first_ios[.identifier] // 0) | .identifier // empty)]
	| select(length == 2) | join(" ")')"
read -r runtime device_type <<<"$pair"
if [[ -z "${runtime:-}" || -z "${device_type:-}" ]]; then
	log "no available iOS runtime with an iPhone device type" >&2
	exit 1
fi

xcrun simctl create "iPhone" "$device_type" "$runtime" >/dev/null
log "created an iPhone simulator ($device_type on $runtime)"
