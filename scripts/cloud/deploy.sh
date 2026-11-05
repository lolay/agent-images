#!/bin/bash
# What the cloud-deploy workflow runs once an image is built and pushed
# (specs/gcp.md, "Deploy: build, smoke, promote"), and make cloud-status.
#
#   deploy.sh type <type> <image@sha256:...|--live> <commit>
#       write agent-<type>-next with that image, run its smoke execution, and
#       only if it passes write agent-<type>. --live reuses agent-<type>'s
#       current image (an env-file-only change).
#   deploy.sh orchestrator <image@sha256:...>
#       point the orchestrator VM at that image; its unit pulls it and restarts
#       the container.
#   deploy.sh prune
#       delete the jobs this repo made (label managed-by=agent-images) whose
#       type has no infra/gcp/<type>.env any more.
#   deploy.sh status
#       read-only: each job's image, commit and last execution, and the
#       orchestrator's image.
#
# Needs gcloud signed in (the workflow's federated identity, or yours for
# status). Jobs go through the Cloud Run v2 REST API (scripts/cloud/job-spec.sh
# says why); gcloud runs the smoke execution and sets the VM's metadata.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly repo
# shellcheck source=hooks/gcp/lib.sh
source "$repo/hooks/gcp/lib.sh"
: "${DEPLOY_POLL_SEC:=3}"
: "${DEPLOY_TIMEOUT_SEC:=600}"

log() { printf 'deploy: %s\n' "$*"; }
die() {
	printf 'deploy: %s\n' "$*" >&2
	exit 1
}

gcp_load_env || die "no infra/gcp/project.env"
readonly run_api="$GCP_RUN_URL/v2/projects/$PROJECT_ID/locations/$REGION"

access_token() { gcloud auth print-access-token --quiet; }

# wait_operation <token>: wait for the long-running operation in GCP_BODY.
wait_operation() {
	local token="$1" operation is_done deadline
	operation="$(jq -r '.name' <<<"$GCP_BODY")"
	is_done="$(jq -r '.done // false' <<<"$GCP_BODY")"
	deadline=$(($(date +%s) + DEPLOY_TIMEOUT_SEC))
	while [[ "$is_done" != "true" ]]; do
		(($(date +%s) < deadline)) || die "timed out waiting for $operation"
		sleep "$DEPLOY_POLL_SEC"
		gcp_call "$token" GET "$GCP_RUN_URL/v2/$operation"
		[[ "$GCP_STATUS" == 200 ]] || die "reading $operation: HTTP $GCP_STATUS: $(gcp_error)"
		is_done="$(jq -r '.done // false' <<<"$GCP_BODY")"
	done
	if jq -e '.error' <<<"$GCP_BODY" >/dev/null; then
		die "$operation failed: $(jq -r '.error.message // .error' <<<"$GCP_BODY")"
	fi
}

# put_job <job> <type> <image> <commit>: create or update the job, and wait.
put_job() {
	local job="$1" type="$2" image="$3" sha="$4" token
	token="$(access_token)"
	gcp_call "$token" PATCH "$run_api/jobs/$job?allowMissing=true" - \
		< <("$repo/scripts/cloud/job-spec.sh" "$type" "$image" --name "$job" --sha "$sha")
	[[ "$GCP_STATUS" == 2* ]] || die "writing $job: HTTP $GCP_STATUS: $(gcp_error)"
	wait_operation "$token"
	log "$job: $image"
}

live_image() {
	local token
	token="$(access_token)"
	gcp_call "$token" GET "$run_api/jobs/agent-$1"
	[[ "$GCP_STATUS" == 200 ]] || die "no live agent-$1 to reuse (HTTP $GCP_STATUS); deploy it with a build first"
	jq -er '.template.template.containers[0].image' <<<"$GCP_BODY"
}

cmd_type() {
	(($# == 3)) || die "usage: deploy.sh type <type> <image@sha256:...|--live> <commit>"
	local type="$1" image="$2" sha="$3"
	[[ -f "$AGENT_GCP_ENV_DIR/$type.env" ]] || die "no infra/gcp/$type.env"
	[[ "$image" != "--live" ]] || image="$(live_image "$type")"
	[[ "$image" == "$ARTIFACT_REGISTRY/"*@sha256:* ]] ||
		die "$image is not a digest under $ARTIFACT_REGISTRY"

	put_job "agent-$type-next" "$type" "$image" "$sha"
	log "agent-$type-next: smoke execution"
	gcloud run jobs execute "agent-$type-next" --project "$PROJECT_ID" --region "$REGION" \
		--args=smoke --wait --quiet ||
		die "smoke failed on agent-$type-next; agent-$type keeps its image (see the execution's logs)"
	put_job "agent-$type" "$type" "$image" "$sha"
}

cmd_orchestrator() {
	(($# == 1)) || die "usage: deploy.sh orchestrator <image@sha256:...>"
	local image="$1"
	# The VM's unit ignores anything else, so refuse it here, where it shows.
	[[ "$image" == "$ARTIFACT_REGISTRY/"*@sha256:* ]] ||
		die "$image is not a digest under $ARTIFACT_REGISTRY"
	gcloud compute instances add-metadata "$ORCHESTRATOR_INSTANCE" --project "$PROJECT_ID" \
		--zone "$ZONE" --metadata "orchestrator-image=$image" --quiet
	log "$ORCHESTRATOR_INSTANCE: orchestrator-image=$image"
}

# list_jobs <token>: every job in the region, one JSON object per line.
list_jobs() {
	local token="$1" page=""
	while :; do
		gcp_call "$token" GET "$run_api/jobs?pageSize=100${page:+&pageToken=$(gcp_uri "$page")}"
		[[ "$GCP_STATUS" == 200 ]] || die "listing jobs: HTTP $GCP_STATUS: $(gcp_error)"
		jq -c '.jobs // [] | .[]' <<<"$GCP_BODY"
		page="$(jq -r '.nextPageToken // empty' <<<"$GCP_BODY")"
		[[ -n "$page" ]] || break
	done
}

cmd_prune() {
	local token job name type
	token="$(access_token)"
	while read -r job; do
		[[ "$(jq -r '.labels["managed-by"] // empty' <<<"$job")" == "agent-images" ]] || continue
		type="$(jq -r '.labels["agent-images-type"] // empty' <<<"$job")"
		name="$(jq -r '.name' <<<"$job")"
		[[ -n "$type" && ! -f "$AGENT_GCP_ENV_DIR/$type.env" ]] || continue
		log "${name##*/}: no infra/gcp/$type.env; deleting"
		gcp_call "$token" DELETE "$GCP_RUN_URL/v2/$name"
		[[ "$GCP_STATUS" == 2* ]] || die "deleting ${name##*/}: HTTP $GCP_STATUS: $(gcp_error)"
		wait_operation "$token"
	done < <(list_jobs "$token")
}

cmd_status() {
	local token type job
	token="$(access_token)"
	while read -r type; do
		for job in "agent-$type" "agent-$type-next"; do
			gcp_call "$token" GET "$run_api/jobs/$job"
			if [[ "$GCP_STATUS" != 200 ]]; then
				printf '%-22s not deployed (HTTP %s)\n' "$job" "$GCP_STATUS"
				continue
			fi
			jq -r --arg job "$job" '
				(.latestCreatedExecution // {}) as $last
				| "\($job | . + " " * (22 - length)) \(.template.template.containers[0].image | split("@")[1][0:19])"
				+ "  commit \(.labels["agent-images-sha"] // "?" | .[0:12])"
				+ "  last run \($last.name // "none" | split("/") | last) \($last.completionStatus // "") \($last.createTime // "")"' \
				<<<"$GCP_BODY"
		done
	done < <(gcp_types)
	local image
	image="$(gcloud compute instances describe "$ORCHESTRATOR_INSTANCE" --project "$PROJECT_ID" \
		--zone "$ZONE" --format=json |
		jq -r '.metadata.items // [] | map(select(.key == "orchestrator-image")) | .[0].value // "not set"')"
	printf '%-22s %s\n' "$ORCHESTRATOR_INSTANCE" "${image##*/}"
}

command="${1:-}"
shift || true
case "$command" in
type) cmd_type "$@" ;;
orchestrator) cmd_orchestrator "$@" ;;
prune) cmd_prune "$@" ;;
status) cmd_status "$@" ;;
*) die "usage: deploy.sh type|orchestrator|prune|status ... (see the header)" ;;
esac
