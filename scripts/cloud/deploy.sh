#!/bin/bash
# What the cloud-deploy workflow runs once an image is built and pushed
# (specs/gcp.md, "Deploy: build, smoke, promote"), and make cloud-status.
#
#   deploy.sh type <type> <image@sha256:...|--live> <commit>
#       write agent-<type>-next with that image, run its smoke execution, and
#       only if it passes write agent-<type>. --live reuses agent-<type>'s
#       current image (an env-file-only change). A type whose env file says
#       DEPLOY=false is built and pushed by the workflow but not deployed: this
#       writes no job and runs no smoke (a GPU type waiting for its quota).
#   deploy.sh vm-build <type> <image name> <commit>
#       a Compute Engine type (BACKEND=compute): build its image with Packer
#       from images/<type>/gce into family <IMAGE_FAMILY>-next, and keep its
#       versions report at out/versions/<type>/versions.md.
#   deploy.sh vm-promote <type> <image name> <commit>
#       boot a smoke VM from that image (agent-gce-smoke: claude, KVM, the
#       emulator's boot time), and only if it passes move the image to family
#       <IMAGE_FAMILY>, which the hook creates VMs from, and delete the type's
#       older images (keep one).
#   deploy.sh orchestrator <image@sha256:...>
#       point the orchestrator VM at that image; its unit pulls it and restarts
#       the container.
#   deploy.sh prune
#       delete the jobs and Compute Engine images this repo made (label
#       managed-by=agent-images) whose type has no infra/gcp/<type>.env any more.
#   deploy.sh status
#       read-only: each job's image, commit and last execution, and the
#       orchestrator's image.
#
# Needs gcloud signed in (the workflow's federated identity, or yours for
# status), and packer for vm-build. Jobs, VMs and images go through the REST
# APIs (scripts/cloud/job-spec.sh says why for jobs); gcloud runs the smoke
# execution and sets the orchestrator VM's metadata.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly repo
# shellcheck source=hooks/gcp/lib.sh
source "$repo/hooks/gcp/lib.sh"
: "${DEPLOY_POLL_SEC:=3}"
: "${DEPLOY_TIMEOUT_SEC:=600}"
: "${DEPLOY_LOG_WAIT_SEC:=5}"
# The smoke execution's own limit, in place of the job's 9.5 h task timeout, so a
# hung smoke can't bill for hours (r1.f8). Every smoke test has 120 s
# (images/toolsets/toolset-checks.sh); twelve at that limit is under 25 min.
: "${DEPLOY_SMOKE_TASK_TIMEOUT_SEC:=1800}"
# The smoke VM boots, then boots the emulator twice (the first from a disk
# Compute Engine is still restoring from the image).
: "${VM_SMOKE_TIMEOUT_SEC:=1500}"
: "${VM_SMOKE_POLL_SEC:=10}"
: "${PACKER:=packer}"

log() { printf 'deploy: %s\n' "$*"; }
die() {
	printf 'deploy: %s\n' "$*" >&2
	exit 1
}

gcp_load_env || die "no infra/gcp/project.env"
readonly run_api="$GCP_RUN_URL/v2/projects/$PROJECT_ID/locations/$REGION"
readonly compute_api="$GCP_COMPUTE_URL/compute/v1/projects/$PROJECT_ID"

access_token() { gcloud auth print-access-token --quiet; }

# backend_of <type>: run (a Cloud Run job, the default) or compute (a VM).
backend_of() { (
	gcp_load_env "$1" || exit 0
	printf '%s' "${BACKEND:-run}"
); }

# deploys <type>: false when the type's env file says DEPLOY=false.
deploys() { (
	gcp_load_env "$1" || exit 0
	[[ "${DEPLOY:-true}" != false ]]
); }

# family_of <type>: a compute type's image family.
family_of() { (
	gcp_load_env "$1" || exit 0
	printf '%s' "${IMAGE_FAMILY:-}"
); }

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

# smoke_log <job>: print the latest execution's log, so a failed smoke shows
# which check failed in the workflow run. Logs land a few seconds after the
# execution ends, so it retries briefly; it never fails the caller.
smoke_log() {
	local job="$1" execution filter lines _
	execution="$(gcloud run jobs executions list --job "$job" --project "$PROJECT_ID" --region "$REGION" \
		--sort-by=~metadata.creationTimestamp --limit 1 --format 'value(metadata.name)' 2>/dev/null)" || execution=""
	if [[ -z "$execution" ]]; then
		log "$job: couldn't find the smoke execution to show its log"
		return 0
	fi
	filter="resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$job\" AND labels.\"run.googleapis.com/execution_name\"=\"$execution\""
	for _ in 1 2 3 4 5 6; do
		lines="$(gcloud logging read "$filter" --project "$PROJECT_ID" --order asc --limit 300 \
			--format 'value(textPayload)' 2>&1)" || {
			log "$execution: can't read its log here ($lines); run: gcloud logging read '$filter' --project $PROJECT_ID --order asc"
			return 0
		}
		[[ -z "$lines" ]] || break
		sleep "$DEPLOY_LOG_WAIT_SEC"
	done
	log "$execution log:"
	printf '%s\n' "${lines:-(empty)}" | sed 's/^/  /'
}

cmd_type() {
	(($# == 3)) || die "usage: deploy.sh type <type> <image@sha256:...|--live> <commit>"
	local type="$1" image="$2" sha="$3"
	[[ -f "$AGENT_GCP_ENV_DIR/$type.env" ]] || die "no infra/gcp/$type.env"
	[[ "$(backend_of "$type")" == run ]] || die "$type is a Compute Engine type; it deploys with vm-build and vm-promote"
	if ! deploys "$type"; then
		log "agent-$type: DEPLOY=false in infra/gcp/$type.env; built $image, not deployed (no job, no smoke)"
		return 0
	fi
	[[ "$image" != "--live" ]] || image="$(live_image "$type")"
	[[ "$image" == "$ARTIFACT_REGISTRY/"*@sha256:* ]] ||
		die "$image is not a digest under $ARTIFACT_REGISTRY"

	put_job "agent-$type-next" "$type" "$image" "$sha"
	log "agent-$type-next: smoke execution"
	if ! gcloud run jobs execute "agent-$type-next" --project "$PROJECT_ID" --region "$REGION" \
		--args=smoke --task-timeout="${DEPLOY_SMOKE_TASK_TIMEOUT_SEC}s" --wait --quiet; then
		smoke_log "agent-$type-next"
		die "smoke failed on agent-$type-next; agent-$type keeps its image"
	fi
	put_job "agent-$type" "$type" "$image" "$sha"
}

# load_compute_type <type>: its settings, or die if it isn't a compute type.
load_compute_type() {
	[[ -f "$AGENT_GCP_ENV_DIR/$1.env" ]] || die "no infra/gcp/$1.env"
	[[ "$(backend_of "$1")" == compute ]] || die "$1 isn't a Compute Engine type (BACKEND=compute)"
	gcp_load_env "$1"
	: "${IMAGE_FAMILY:?}" "${MACHINE_TYPE:?}" "${DISK_GB:?}" "${DISK_TYPE:?}" "${ZONE:?}" "${NETWORK:?}" "${SUBNET:?}"
}

valid_image_name() { [[ "$1" =~ ^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$ ]]; }

cmd_vm_build() {
	(($# == 3)) || die "usage: deploy.sh vm-build <type> <image name> <commit>"
	local type="$1" image="$2" sha="$3" dir="$repo/images/$1/gce"
	# Packer downloads the image's versions report here (build.pkr.hcl); the
	# deploy workflow's summary and artifact steps read it from out/ at the repo root.
	local report="$repo/out/versions/$1/versions.md"
	load_compute_type "$type"
	valid_image_name "$image" || die "$image is not a valid image name"
	[[ -d "$dir" ]] || die "no images/$type/gce"
	mkdir -p "$(dirname "$report")"
	rm -f "$report"
	"$PACKER" init "$dir"
	# No -force: image names are unique per run, and replacing a same-named
	# image would delete the live one.
	"$PACKER" build -color=false \
		-var "project_id=$PROJECT_ID" -var "zone=$ZONE" -var "network=$NETWORK" -var "subnetwork=$SUBNET" \
		-var "image_name=$image" -var "image_family=$IMAGE_FAMILY-next" -var "commit=$sha" \
		-var "machine_type=$MACHINE_TYPE" -var "disk_size_gb=$DISK_GB" -var "versions_file=$report" "$dir"
	[[ -s "$report" ]] || die "the build left no versions report at $report"
	log "$image: built into family $IMAGE_FAMILY-next"
}

# guest_attributes <token> <zone url> <instance>: its agent-images/* guest
# attributes as one JSON object ({} if none yet).
guest_attributes() {
	gcp_call "$1" GET "$2/instances/$3/getGuestAttributes?queryPath=$(gcp_uri agent-images/)"
	if [[ "$GCP_STATUS" != 200 ]]; then
		printf '{}'
		return
	fi
	jq -c '[.queryValue.items // [] | .[] | {key, value: (.value | rtrimstr("\n"))}] | from_entries' <<<"$GCP_BODY"
}

# delete_instance <token> <zone url> <instance>: delete it and wait; a missing
# one is fine.
delete_instance() {
	gcp_call "$1" DELETE "$2/instances/$3"
	case "$GCP_STATUS" in
	2*) gcp_compute_wait "$1" "$2" 300 || log "$3: delete still running or failed (${GCP_OP_ERROR:-timed out})" ;;
	404) ;;
	*) log "$3: couldn't delete: HTTP $GCP_STATUS: $(gcp_error)" ;;
	esac
}

cmd_vm_promote() {
	(($# == 3)) || die "usage: deploy.sh vm-promote <type> <image name> <commit>"
	local type="$1" image="$2" sha="$3" attributes result deadline
	load_compute_type "$type"
	valid_image_name "$image" || die "$image is not a valid image name"
	# Not local: the EXIT trap that deletes the smoke VM runs after this returns.
	token="$(access_token)"
	zone_url="$compute_api/zones/$ZONE"
	smoke="agent-$type-smoke-${sha:0:12}"

	# A rerun of this commit may have left one.
	delete_instance "$token" "$zone_url" "$smoke"
	trap 'delete_instance "$token" "$zone_url" "$smoke"' EXIT
	log "$smoke: booting $image"
	# No service account: the smoke needs no GCP access, and reports through
	# guest attributes, which need none.
	gcp_call "$token" POST "$zone_url/instances" - < <(
		gcp_instance_body "$smoke" "projects/$PROJECT_ID/global/images/$image" "$type" "$((VM_SMOKE_TIMEOUT_SEC + 600))" |
			jq -c '.labels["agent-images-role"] = "smoke" | .metadata.items += [{key: "agent-smoke", value: "true"}]'
	)
	[[ "$GCP_STATUS" == 2* ]] || die "creating $smoke: HTTP $GCP_STATUS: $(gcp_error)"
	gcp_compute_wait "$token" "$zone_url" 300 || die "creating $smoke: ${GCP_OP_ERROR:-timed out}"

	log "$smoke: waiting for its smoke checks (up to ${VM_SMOKE_TIMEOUT_SEC}s)"
	deadline=$(($(date +%s) + VM_SMOKE_TIMEOUT_SEC))
	while :; do
		attributes="$(guest_attributes "$token" "$zone_url" "$smoke")"
		result="$(jq -r '.smoke // empty' <<<"$attributes")"
		[[ -z "$result" ]] || break
		(($(date +%s) < deadline)) || die "$smoke: no smoke result after ${VM_SMOKE_TIMEOUT_SEC}s; $IMAGE_FAMILY keeps its image"
		gcp_call "$token" GET "$zone_url/instances/$smoke"
		if [[ "$GCP_STATUS" == 200 && "$(jq -r '.status' <<<"$GCP_BODY")" == TERMINATED ]]; then
			die "$smoke: powered off without a smoke result; $IMAGE_FAMILY keeps its image"
		fi
		sleep "$VM_SMOKE_POLL_SEC"
	done
	log "$smoke: smoke $result"
	jq -r '."smoke-log" // "(no log)"' <<<"$attributes" | sed 's/^/  /'
	if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
		jq -r --arg image "$image" '"\($image): smoke \(.smoke)", "",
			"| Measure | Value |", "| --- | --- |",
			(to_entries[] | select(.key != "smoke" and .key != "smoke-log") | "| \(.key) | \(.value) |")' \
			<<<"$attributes" >>"$GITHUB_STEP_SUMMARY"
	fi
	[[ "$result" == pass ]] || die "smoke failed on $image; $IMAGE_FAMILY keeps its image"

	gcp_call "$token" PATCH "$compute_api/global/images/$image" - < <(jq -cn --arg family "$IMAGE_FAMILY" '{family: $family}')
	[[ "$GCP_STATUS" == 2* ]] || die "moving $image to $IMAGE_FAMILY: HTTP $GCP_STATUS: $(gcp_error)"
	gcp_compute_wait "$token" "$compute_api/global" 300 || die "moving $image to $IMAGE_FAMILY: ${GCP_OP_ERROR:-timed out}"
	log "$image: now the latest in family $IMAGE_FAMILY"

	# Keep one: VMs already running keep their own disks.
	local old
	while read -r old; do
		[[ "$old" != "$image" ]] || continue
		gcp_call "$token" DELETE "$compute_api/global/images/$old"
		if [[ "$GCP_STATUS" == 2* || "$GCP_STATUS" == 404 ]]; then
			log "$old: deleted"
		else
			log "$old: couldn't delete: HTTP $GCP_STATUS: $(gcp_error)"
		fi
	done < <(list_images "$token" "labels.agent-images-type=$type" | jq -r '.name')
}

# list_images <token> <filter>: this project's images matching the filter, one
# JSON object per line.
list_images() {
	local token="$1" filter="$2" page=""
	while :; do
		gcp_call "$token" GET "$compute_api/global/images?filter=$(gcp_uri "$filter")${page:+&pageToken=$(gcp_uri "$page")}"
		[[ "$GCP_STATUS" == 200 ]] || die "listing images: HTTP $GCP_STATUS: $(gcp_error)"
		jq -c '.items // [] | .[]' <<<"$GCP_BODY"
		page="$(jq -r '.nextPageToken // empty' <<<"$GCP_BODY")"
		[[ -n "$page" ]] || break
	done
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
	local image
	while read -r image; do
		type="$(jq -r '.labels["agent-images-type"] // empty' <<<"$image")"
		name="$(jq -r '.name' <<<"$image")"
		[[ -n "$type" && ! -f "$AGENT_GCP_ENV_DIR/$type.env" ]] || continue
		log "$name: no infra/gcp/$type.env; deleting the image"
		gcp_call "$token" DELETE "$compute_api/global/images/$name"
		[[ "$GCP_STATUS" == 2* || "$GCP_STATUS" == 404 ]] || die "deleting $name: HTTP $GCP_STATUS: $(gcp_error)"
	done < <(list_images "$token" "labels.managed-by=agent-images")
}

cmd_status() {
	local token type job
	token="$(access_token)"
	while read -r type; do
		if [[ "$(backend_of "$type")" == compute ]]; then
			local family
			family="$(family_of "$type")"
			gcp_call "$token" GET "$compute_api/global/images/family/$family"
			if [[ "$GCP_STATUS" != 200 ]]; then
				printf '%-22s no image in family %s (HTTP %s)\n' "$type" "$family" "$GCP_STATUS"
				continue
			fi
			jq -r --arg type "$type" --arg family "$family" '
				"\($type | . + " " * (22 - length)) \(.name) (family \($family))"
				+ "  commit \(.labels["agent-images-sha"] // "?" | .[0:12])  created \(.creationTimestamp)"' <<<"$GCP_BODY"
			continue
		fi
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
vm-build) cmd_vm_build "$@" ;;
vm-promote) cmd_vm_promote "$@" ;;
orchestrator) cmd_orchestrator "$@" ;;
prune) cmd_prune "$@" ;;
status) cmd_status "$@" ;;
*) die "usage: deploy.sh type|vm-build|vm-promote|orchestrator|prune|status ... (see the header)" ;;
esac
