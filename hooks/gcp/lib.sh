# shellcheck shell=bash
# Sourced by hooks/gcp/spawn-runner and the orchestrator's supervisor
# (images/cloud/orchestrator/supervisor.sh). GCP REST calls with curl and a token
# from the metadata server: the orchestrator host has no gcloud, which would
# cost about 100 MB of Python per call on an e2-micro.
#
# The layout matches the repo's, in the image too: <root>/hooks/gcp/lib.sh and
# <root>/infra/gcp/*.env. The URLs are overridable so the tests can point them at
# a fake (tests/unit/fake_gcp.py).

gcp_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${AGENT_GCP_ENV_DIR:=$gcp_lib_dir/../../infra/gcp}"
: "${GCP_METADATA_URL:=http://metadata.google.internal/computeMetadata/v1}"
: "${GCP_STORAGE_URL:=https://storage.googleapis.com}"
: "${GCP_RUN_URL:=https://run.googleapis.com}"
: "${GCP_SECRETS_URL:=https://secretmanager.googleapis.com}"
: "${GCP_COMPUTE_URL:=https://compute.googleapis.com}"
: "${GCP_POLL_SEC:=2}"
: "${GCP_CURL_MAX_TIME:=20}"

# gcp_load_env [type]: export project.env, then infra/gcp/<type>.env if given.
gcp_load_env() {
	local file
	for file in "$AGENT_GCP_ENV_DIR/project.env" ${1:+"$AGENT_GCP_ENV_DIR/$1.env"}; do
		[[ -f "$file" ]] || return 1
		set -a
		# shellcheck source=/dev/null
		source "$file"
		set +a
	done
}

# gcp_types: every runner type, one per line in byte order (an env file other
# than project.env).
gcp_types() {
	local file type
	for file in "$AGENT_GCP_ENV_DIR"/*.env; do
		type="$(basename "$file" .env)"
		[[ "$type" == "project" ]] || printf '%s\n' "$type"
	done | LC_ALL=C sort
}

# gcp_uri <text>: percent-encode for a URL path segment or query value.
gcp_uri() { jq -rn --arg value "$1" '$value | @uri'; }

# gcp_token: print an access token for the VM's service account.
gcp_token() {
	curl -sSf --max-time "$GCP_CURL_MAX_TIME" -H 'Metadata-Flavor: Google' \
		"$GCP_METADATA_URL/instance/service-accounts/default/token" |
		jq -er '.access_token'
}

# gcp_call <token> <method> <url> [json body on stdin with "-"]
# Sets GCP_STATUS (000 if curl couldn't connect) and GCP_BODY in the calling
# shell, so feed a body with `< <(...)`, not a pipe. The token goes in
# through a file descriptor, so it never appears in a process list, and nothing
# here prints a request body.
gcp_call() {
	local token="$1" method="$2" url="$3" response
	local args=(-sS -X "$method" --max-time "$GCP_CURL_MAX_TIME" -w '\n%{http_code}')
	if [[ "${4:-}" == "-" ]]; then
		args+=(-H 'Content-Type: application/json' --data-binary @-)
	fi
	# The process substitution stays on curl's own command line: bash closes it
	# once the command that made it ends.
	response="$(curl "${args[@]}" -H @<(printf 'Authorization: Bearer %s\n' "$token") "$url")" ||
		response=$'\n000'
	# shellcheck disable=SC2034 # read by the caller
	GCP_STATUS="${response##*$'\n'}"
	GCP_BODY="${response%$'\n'*}"
}

# gcp_error: the API's error message from GCP_BODY, or the body's start.
gcp_error() {
	local message
	message="$(jq -r '.error.message // empty' <<<"$GCP_BODY" 2>/dev/null)"
	printf '%s' "${message:-${GCP_BODY:0:200}}"
}

# gcp_compute_wait <token> <scope url> <seconds>: poll the Compute Engine
# operation in GCP_BODY (an insert's or delete's response) until it's DONE or
# the time is up. <scope url> is .../compute/v1/projects/<p>/zones/<z> or
# .../global. Returns 0 when it finished cleanly; 1 when it failed, with its
# first error in GCP_OP_ERROR ("<CODE>: <message>"); 2 when time ran out.
gcp_compute_wait() {
	local token="$1" scope="$2" deadline name status
	deadline=$(($(date +%s) + $3))
	name="$(jq -r '.name // empty' <<<"$GCP_BODY")"
	status="$(jq -r '.status // empty' <<<"$GCP_BODY")"
	GCP_OP_ERROR=""
	while [[ "$status" != "DONE" ]]; do
		(($(date +%s) < deadline)) || return 2
		sleep "$GCP_POLL_SEC"
		gcp_call "$token" GET "$scope/operations/$name"
		[[ "$GCP_STATUS" == 200 ]] || continue
		status="$(jq -r '.status // empty' <<<"$GCP_BODY")"
	done
	GCP_OP_ERROR="$(jq -r '.error.errors[0] // empty | "\(.code): \(.message)"' <<<"$GCP_BODY")"
	[[ -z "$GCP_OP_ERROR" ]] || return 1
}

# gcp_instance_body <name> <source image> <type> <max run sec> [service account]
# Print the Compute Engine instance a compute type runs on, from its env file
# (MACHINE_TYPE, DISK_GB, DISK_TYPE) and project.env (ZONE, NETWORK, SUBNET):
# nested virtualization for the emulator, no external IP (egress through Cloud
# NAT), deleted by Compute Engine at <max run sec> whatever happens, guest
# attributes on and the serial console sent to Cloud Logging. With no service
# account the VM gets none. Callers add their own labels and metadata items;
# nothing secret goes through here (arguments are visible in a process list).
gcp_instance_body() {
	local name="$1" image="$2" type="$3" max_run="$4" account="${5:-}"
	jq -cn --arg name "$name" --arg image "$image" --arg type "$type" --arg max_run "$max_run" \
		--arg account "$account" --arg zone "$ZONE" --arg machine "$MACHINE_TYPE" \
		--arg disk_gb "$DISK_GB" --arg disk_type "$DISK_TYPE" --arg network "$NETWORK" --arg subnet "$SUBNET" '
		{
			name: $name,
			machineType: "zones/\($zone)/machineTypes/\($machine)",
			labels: {"managed-by": "agent-images", "agent-images-type": $type},
			disks: [{boot: true, autoDelete: true, initializeParams: {
				sourceImage: $image, diskSizeGb: $disk_gb, diskType: "zones/\($zone)/diskTypes/\($disk_type)"}}],
			networkInterfaces: [{network: $network, subnetwork: $subnet}],
			serviceAccounts: (if $account == "" then []
				else [{email: $account, scopes: ["https://www.googleapis.com/auth/cloud-platform"]}] end),
			advancedMachineFeatures: {enableNestedVirtualization: true},
			scheduling: {provisioningModel: "STANDARD", automaticRestart: false,
				maxRunDuration: {seconds: $max_run}, instanceTerminationAction: "DELETE"},
			metadata: {items: [
				{key: "enable-guest-attributes", value: "TRUE"},
				{key: "serial-port-logging-enable", value: "TRUE"}
			]}
		}'
}
