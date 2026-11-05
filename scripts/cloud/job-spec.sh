#!/bin/bash
# Prints the Cloud Run v2 Job for a runner type, from infra/gcp/project.env and
# infra/gcp/<type>.env (specs/gcp.md, "Deploy"). scripts/cloud/deploy.sh PATCHes
# it with allowMissing=true, so the same body creates or updates the job.
#
#   job-spec.sh <type> <image@sha256:...> [--name <job>] [--sha <commit>]
#
# REST rather than `gcloud run jobs deploy`: gcloud's --add-volume has no
# disk-backed type, and scratch must not be memory. The API's emptyDir takes
# medium DISK.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly repo
# shellcheck source=hooks/gcp/lib.sh
source "$repo/hooks/gcp/lib.sh"

usage() {
	printf 'usage: %s <type> <image@sha256:...> [--name <job>] [--sha <commit>]\n' "$(basename "$0")" >&2
	exit 2
}

(($# >= 2)) || usage
readonly type="$1" image="$2"
shift 2
name="agent-$type"
sha=""
while (($# > 0)); do
	case "$1" in
	--name) name="${2:?}" ;;
	--sha) sha="${2:?}" ;;
	*) usage ;;
	esac
	shift 2
done

[[ "$image" == *@sha256:* ]] || {
	printf 'job-spec: %s is not a digest reference (name@sha256:...)\n' "$image" >&2
	exit 2
}
gcp_load_env "$type" || {
	printf 'job-spec: no infra/gcp/%s.env\n' "$type" >&2
	exit 2
}

jq -n \
	--arg name "projects/$PROJECT_ID/locations/$REGION/jobs/$name" \
	--arg type "$type" \
	--arg sha "$sha" \
	--arg image "$image" \
	--arg service_account "$RUNNER_SERVICE_ACCOUNT" \
	--arg cpu "${CPU:?}" \
	--arg memory "${MEMORY:?}" \
	--arg scratch "${SCRATCH_GIB:?}Gi" \
	--arg timeout "${TASK_TIMEOUT_SEC:?}s" \
	--arg launch_stage "${LAUNCH_STAGE:-}" \
	--arg outputs_bucket "$OUTPUTS_BUCKET" \
	--arg datasets_bucket "$DATASETS_BUCKET" \
	--arg task_timeout "$TASK_TIMEOUT_SEC" \
	--arg retire_margin "${RETIRE_MARGIN_SEC:-900}" \
	--arg notice_tz "${AGENT_NOTICE_TZ:-UTC}" \
	--arg exit_if_unused "${EXIT_IF_UNUSED_MIN-}" \
	--arg release_idle "${RELEASE_IDLE_SESSION_MIN-60}" \
	--arg kill_after "${KILL_SESSION_AFTER_MIN-480}" \
	'
	def env_list: to_entries | map({name: .key, value: .value});
	{
		name: $name,
		labels: ({"managed-by": "agent-images", "agent-images-type": $type}
			+ (if $sha == "" then {} else {"agent-images-sha": $sha} end)),
		template: {
			taskCount: 1,
			parallelism: 1,
			template: {
				serviceAccount: $service_account,
				executionEnvironment: "EXECUTION_ENVIRONMENT_GEN2",
				# A retried task would come back with a spent work order.
				maxRetries: 0,
				timeout: $timeout,
				containers: [{
					name: "runner",
					image: $image,
					resources: {limits: {cpu: $cpu, memory: $memory}},
					env: ({
						AGENT_TYPE: $type,
						AGENT_TASK_TIMEOUT_SEC: $task_timeout,
						AGENT_RETIRE_MARGIN_SEC: $retire_margin,
						AGENT_NOTICE_TZ: $notice_tz,
						AGENT_OUTPUTS_BUCKET: $outputs_bucket,
						CLAUDE_EXIT_IF_UNUSED_MIN: $exit_if_unused,
						CLAUDE_RELEASE_IDLE_SESSION_MIN: $release_idle,
						CLAUDE_KILL_SESSION_AFTER_MIN: $kill_after
					} | env_list),
					volumeMounts: [
						{name: "scratch", mountPath: "/scratch"},
						{name: "outputs", mountPath: "/mnt/outputs"},
						{name: "datasets", mountPath: "/mnt/datasets"}
					]
				}],
				volumes: [
					{name: "scratch", emptyDir: {medium: "DISK", sizeLimit: $scratch}},
					# Cloud Run mounts as the container user by default (uid 1000, mode
					# 777/666); implicit-dirs shows folders other sessions made.
					{name: "outputs", gcs: {bucket: $outputs_bucket, readOnly: false,
						mountOptions: ["implicit-dirs"]}},
					{name: "datasets", gcs: {bucket: $datasets_bucket, readOnly: true,
						mountOptions: ["implicit-dirs"]}}
				]
			}
		}
	}
	+ (if $launch_stage == "" then {} else {launchStage: $launch_stage} end)'
