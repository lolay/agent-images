#!/bin/bash
# agent-gce-order: configures an android runner VM on Compute Engine from its
# instance metadata at boot, once (specs/gcp.md, "Compute Engine VM per
# session"). The counterpart of what scripts/linux/vm-configure.sh does to an
# LXD VM from the host. Run as root by agent-gce-order.service; installed as
# /usr/local/libexec/agent-images/agent-gce-order.
#
# Metadata the hook (hooks/gcp/spawn-runner) sets on the instance:
#   work-order    the single-use work order: written to the guest user's
#                 ~/.claude-runner/environment-secret (mode 600), never printed
#   runner-env    KEY=value lines for runner.env (AGENT, RUNNER_LABEL, limits)
# Then it writes runner.env with EPHEMERAL=true, which starts agent-runner
# (agent-runner.path): one session, then the VM powers off and the reaper in the
# orchestrator container deletes it.
#
# The guest doesn't delete the work-order key: that would need setMetadata on
# the runner's service account, which would let one session rewrite another
# session VM's metadata. It sets guest attribute agent-images/order=read
# instead, and the reaper removes the key. The order is spent at registration
# anyway.
#
# Other modes, from metadata:
#   agent-smoke=true   the deploy's smoke VM: run agent-gce-smoke as the guest
#                      user, report through guest attributes, power off
#   agent-debug=true   do nothing, stay up (a VM started by hand to look around)
# With neither and no work order, there's nothing to do: power off.
set -euo pipefail

# Overridable for the tests (tests/unit/test_gce_order.py).
: "${GCP_METADATA_URL:=http://metadata.google.internal/computeMetadata/v1}"
: "${AGENT_GUEST_USER:=ubuntu}"
: "${AGENT_GUEST_HOME:=$(getent passwd "$AGENT_GUEST_USER" | cut -d: -f6)}"
: "${AGENT_POWEROFF_COMMAND:=systemctl poweroff}"
: "${AGENT_SMOKE_COMMAND:=}"
: "${AGENT_METADATA_RETRIES:=30}"

readonly libexec_dir=/usr/local/libexec/agent-images
readonly config_dir="$AGENT_GUEST_HOME/.config/agent-runner"
readonly runner_env="$config_dir/runner.env"
readonly secret_dir="$AGENT_GUEST_HOME/.claude-runner"

log() { printf 'agent-gce-order: %s\n' "$*"; }

poweroff() {
	log "powering off"
	# shellcheck disable=SC2086 # a command line, split on purpose
	exec $AGENT_POWEROFF_COMMAND
}

# Any unexpected failure powers the VM off, so the reaper deletes it within a
# minute and the orchestrator retries the session with a new order, rather
# than the VM idling until its max run duration.
trap 'status=$?; ((status == 0)) || { log "failed (exit $status)"; poweroff; }' EXIT

# metadata <key>: print an instance attribute; exit 1 if it isn't set. Retries
# while the metadata server isn't reachable yet (early boot).
metadata() {
	local body status
	body="$(mktemp)"
	chmod 600 "$body"
	status="$(curl -sS -o "$body" -w '%{http_code}' --retry "$AGENT_METADATA_RETRIES" --retry-connrefused \
		--retry-delay 1 --max-time 10 -H 'Metadata-Flavor: Google' \
		"$GCP_METADATA_URL/instance/attributes/$1")" || status=000
	[[ "$status" == 200 ]] && cat "$body"
	rm -f "$body"
	[[ "$status" == 200 ]]
}

# metadata_value <key>: the attribute, or empty if it isn't set.
metadata_value() {
	local value
	value="$(metadata "$1")" || return 0
	printf '%s' "$value"
}

# guest_attribute <key> <value>: publish under agent-images/ for the reaper and
# the deploy (needs enable-guest-attributes=TRUE, which the hook and the deploy
# set). Values are visible to anyone who can read the instance; never secrets.
guest_attribute() {
	curl -sSf --max-time 10 -X PUT -H 'Metadata-Flavor: Google' --data-binary @- \
		"$GCP_METADATA_URL/instance/guest-attributes/agent-images/$1" <<<"$2" >/dev/null
}

if [[ -f "$runner_env" ]]; then
	# A reboot of a VM that's already configured: its runner starts on its own.
	log "already configured"
	trap - EXIT
	exit 0
fi

if [[ "$(metadata_value agent-debug)" == "true" ]]; then
	log "agent-debug=true: leaving the VM as it is"
	trap - EXIT
	exit 0
fi

if [[ "$(metadata_value agent-smoke)" == "true" ]]; then
	log "smoke mode"
	output="$(mktemp)"
	if [[ -n "$AGENT_SMOKE_COMMAND" ]]; then
		smoke=("$AGENT_SMOKE_COMMAND")
	else
		smoke=(runuser -l "$AGENT_GUEST_USER" -c "$libexec_dir/agent-gce-smoke")
	fi
	if "${smoke[@]}" >"$output" 2>&1; then result=pass; else result=fail; fi
	cat "$output"
	# key=value lines from the smoke (timings) become their own attributes.
	while IFS='=' read -r key value; do
		guest_attribute "$key" "$value"
	done < <(grep -E '^[a-z][a-z0-9-]*=[^=]*$' "$output" || true)
	guest_attribute smoke-log "$(tail -c 8000 "$output")"
	# Written last: the deploy waits for this one.
	guest_attribute smoke "$result"
	log "smoke: $result"
	rm -f "$output"
	poweroff
fi

order_file="$(mktemp)"
chmod 600 "$order_file"
if ! metadata work-order >"$order_file" || [[ ! -s "$order_file" ]]; then
	rm -f "$order_file"
	log "no work order in the instance metadata; nothing to do"
	poweroff
fi

uid="$(id -u "$AGENT_GUEST_USER")"
gid="$(id -g "$AGENT_GUEST_USER")"
install -d -m 700 -o "$uid" -g "$gid" "$secret_dir"
install -d -m 755 -o "$uid" -g "$gid" "$config_dir"
install -m 600 -o "$uid" -g "$gid" "$order_file" "$secret_dir/environment-secret"
rm -f "$order_file"

# runner.env from the hook's settings. agent-runner sources it as the guest
# user, so only plain KEY=value lines pass; anything else is dropped and named.
env_tmp="$config_dir/.runner.env.tmp"
{
	printf 'AGENT=claude\n'
	while IFS= read -r line || [[ -n "$line" ]]; do
		[[ -n "$line" ]] || continue
		if [[ "$line" =~ ^[A-Z_][A-Z0-9_]*=[A-Za-z0-9_./:@+,-]*$ ]]; then
			printf '%s\n' "$line"
		else
			log "runner-env: dropping a line that isn't KEY=value (${line%%=*})" >&2
		fi
	done < <(metadata_value runner-env)
	printf 'EPHEMERAL=true\n'
} >"$env_tmp"
chown "$uid:$gid" "$env_tmp"
chmod 644 "$env_tmp"
# The rename is what agent-runner.path sees: the runner never reads half a file.
mv "$env_tmp" "$runner_env"

guest_attribute order read
log "configured: $(grep -E '^RUNNER_LABEL=' "$runner_env" || echo 'no RUNNER_LABEL')"
trap - EXIT
