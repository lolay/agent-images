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

# gcp_type_setting <type> <NAME> <default>: one key from infra/gcp/<type>.env
# (with project.env under it), or the default when the file leaves it unset or
# empty. Prints nothing when the type has no env file. A subshell, so nothing
# the file sets leaks into the caller.
gcp_type_setting() { (
	gcp_load_env "$1" >/dev/null 2>&1 || exit 0
	printf '%s' "${!2:-$3}"
); }

# The per-type settings more than one script reads, each with its one default.
# gcp_backend <type>: run (a Cloud Run job) or compute (a VM per session).
gcp_backend() { gcp_type_setting "$1" BACKEND run; }
# gcp_rebuild_class <type>: the scheduled rebuild it takes, nightly or weekly
# (specs/gcp.md, "Rebuild cadence"). Unset is nightly, so a new type is never
# left off the schedule.
gcp_rebuild_class() { gcp_type_setting "$1" REBUILD nightly; }
# gcp_deploys <type>: false only when the env file says DEPLOY=false (a type
# that builds but isn't deployed, waiting for its quota).
gcp_deploys() { [[ "$(gcp_type_setting "$1" DEPLOY true)" != false ]]; }

# gcp_image_family <type>: a compute type's image family, always agent-<type>,
# as a Cloud Run type's job is: deploy.sh builds into <family>-next and promotes
# into it, the hook creates VMs from it, and the supervisor waits for it.
# Derived, not set: an env file's IMAGE_FAMILY reached Packer only at the next
# image build, so renaming it broke every spawn until then (r1.f20).
gcp_image_family() { printf 'agent-%s\n' "$1"; }

# gcp_project_keys: every key project.env sets, one per line. These are the
# project keys a type's env file may not set (gcp_check_type). The list is
# project.env's own rather than a copy here: project.env is Terraform's outputs
# for the one project and holds nothing per type (its header says so), and
# deploy.sh and the orchestrator's reaper read it alone, so a type's override of
# any key in it would reach spawn-runner and job-spec.sh but not them (#161). A
# new Terraform output is covered the day it's copied in.
gcp_project_keys() {
	[[ -f "$AGENT_GCP_ENV_DIR/project.env" ]] || return 0
	sed -n 's/^\(export[[:space:]]\{1,\}\)\{0,1\}\([A-Za-z_][A-Za-z0-9_]*\)=.*/\2/p' \
		"$AGENT_GCP_ENV_DIR/project.env"
}

# gcp_type_project_keys <type>: the project keys infra/gcp/<type>.env itself
# sets, one per line. The type's file alone, sourced with the project keys
# unset, not gcp_type_setting: that layers project.env underneath, and the
# callers have it loaded already, so every type would look as if it set them.
gcp_type_project_keys() { (
	local key keys=()
	mapfile -t keys < <(gcp_project_keys)
	((${#keys[@]} > 0)) || exit 0
	unset "${keys[@]}"
	# shellcheck source=/dev/null
	source "$AGENT_GCP_ENV_DIR/$1.env" >/dev/null 2>&1 || exit 0
	for key in "${keys[@]}"; do
		[[ ! -v "$key" ]] || printf '%s\n' "$key"
	done
); }

# gcp_check_type <type>: fail, with the one reason on stderr, when the type's env
# file is something the pipeline can't serve: DEPLOY=false on a BACKEND=compute
# type, which deploy.sh would build and promote with no orchestrator to serve
# it; an IMAGE_FAMILY, which nothing reads (gcp_image_family); or a project key
# (gcp_project_keys), which only some readers would honor. The last two are
# refused rather than ignored: a setting that silently did nothing, or did it in
# half the pipeline, would look as if it had worked. Every reader of the rules
# calls this: deploy.sh refuses the type, the supervisor skips it, and
# `make cloud-test` runs it over the real infra/gcp/*.env, so a bad file fails
# its pull request rather than reaching the host (an env-file-only change plans
# no type deploy, r3.f2).
gcp_check_type() {
	local key
	if [[ "$(gcp_backend "$1")" == compute ]] && ! gcp_deploys "$1"; then
		printf '%s: DEPLOY=false is for Cloud Run types only, not BACKEND=compute (a Compute Engine type would be built and promoted with no orchestrator to serve it); remove it from infra/gcp/%s.env\n' "$1" "$1" >&2
		return 1
	fi
	if [[ -n "$(gcp_type_setting "$1" IMAGE_FAMILY "")" ]]; then
		printf "%s: IMAGE_FAMILY is not a setting: a type's image family is always %s; remove it from infra/gcp/%s.env\n" \
			"$1" "$(gcp_image_family "$1")" "$1" >&2
		return 1
	fi
	key="$(gcp_type_project_keys "$1" | head -n 1)"
	if [[ -n "$key" ]]; then
		printf "%s: %s is a project key: it belongs in infra/gcp/project.env only, which deploy.sh and the orchestrator's reaper read alone; remove it from infra/gcp/%s.env\n" \
			"$1" "$key" "$1" >&2
		return 1
	fi
}

# gcp_runner_settings <type>: the runner.env settings
# (images/shared/runners/claude.sh) for the loaded type, one KEY=value line
# each, for both backends: the Cloud Run job's env (scripts/cloud/job-spec.sh)
# and a compute VM's runner-env metadata (hooks/gcp/spawn-runner). A key the env
# file doesn't set is left out, so claude.sh's default applies; set but empty
# means "never". A nightly Cloud Run type runs the Claude Code its lock baked in
# (CLAUDE_UPGRADE=false): the watch moves it within the hour, so a session
# updating itself would only run something nothing smoked (specs/gcp.md, m5,
# D6). A weekly type keeps updating itself at start, and so does a compute type
# whatever its class: the watch locks only Cloud Run types, so its Claude is
# whatever its last Packer build got (r3.f9).
gcp_runner_settings() {
	local key
	for key in EXIT_IF_UNUSED_MIN RELEASE_IDLE_SESSION_MIN KILL_SESSION_AFTER_MIN; do
		if [[ -v "$key" ]]; then
			printf 'CLAUDE_%s=%s\n' "$key" "${!key}"
		fi
	done
	if [[ "$(gcp_backend "$1")" == run && "$(gcp_rebuild_class "$1")" == nightly ]]; then
		printf 'CLAUDE_UPGRADE=false\n'
	fi
}

# gcp_retire_margin <hard stop sec>: print how long before the hard stop (the
# job's task timeout, a VM's max run duration) the runner retires:
# RETIRE_MARGIN_SEC from the loaded env file, 900 by default. Fails, with the
# reason on stderr, unless the margin is a number less than the hard stop, so
# the runner would retire before it started.
gcp_retire_margin() {
	local stop="$1" margin="${RETIRE_MARGIN_SEC:-900}"
	if [[ ! "$stop" =~ ^[0-9]+$ || ! "$margin" =~ ^[0-9]+$ ]] || ((margin >= stop)); then
		printf 'the retire margin (%s s) must be less than the hard stop (%s s)\n' "$margin" "$stop" >&2
		return 1
	fi
	printf '%s\n' "$margin"
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
