#!/bin/bash
# The agent-orchestrator container's entrypoint (specs/gcp.md, "The orchestrator
# container"). One `claude self-hosted-runner orchestrator` per runner type, since
# each registers with one Claude environment's key:
#
#   - types: every infra/gcp/*.env baked into the image except project.env;
#   - secrets: claude-env-<type> from Secret Manager (REST, metadata-server
#     token), written to a file per type in /dev/shm, mode 600; each orchestrator
#     is pointed at its own file only. A type whose secret can't be read is
#     retried; the others start anyway;
#   - readiness: a type starts only once it has something to spawn, its Cloud
#     Run job agent-<type> or, for BACKEND=compute, an image in family
#     agent-<type>. Until then it's checked again every RUNNER_WAIT_SEC, on the
#     same loop that retries secrets, so the deploy needn't hold the
#     orchestrator back for a type whose first build failed (r1.f3);
#   - supervision: restart an orchestrator that exits, with backoff; prefix each
#     one's log lines with its type; on SIGTERM, pass it to every orchestrator at
#     once and exit when they have (they drain for 70 s; the host's docker stop
#     timeout is 85 s);
#   - memory: every MEMORY_LOG_INTERVAL_SEC, one line per type with the Pss and
#     Rss of its orchestrator and child processes (/proc/<pid>/smaps_rollup) and
#     whether its /healthz says connected, then a total with the VM's available
#     memory. Read them from Cloud Logging: "supervisor: memory".
#   - reaper: every REAPER_INTERVAL_SEC, for each type with BACKEND=compute, it
#     deletes that type's VMs that have stopped (a runner VM powers off after
#     its session) or outlived MAX_RUN_DURATION_SEC, and removes the work-order
#     metadata key from VMs whose guest has marked it read (guest attribute
#     agent-images/order). The VMs can't delete themselves: that would put a
#     delete permission inside the session. Read it from Cloud Logging:
#     "supervisor: reaper".
#
# One loop owns every child: it waits for whichever of the orchestrators, its
# own tick timer or the reaper finishes first (wait -n), so signals, restarts
# and the memory log never race each other. The reaper runs in the background,
# so a slow API call can't hold up a signal.
set -euo pipefail

: "${AGENT_ORCHESTRATOR_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
: "${CLAUDE_BIN:=claude}"
: "${SUPERVISOR_SECRETS_DIR:=/dev/shm/agent-orchestrator}"
: "${HEALTH_PORT_BASE:=8080}"
: "${SHUTDOWN_DRAIN_SEC:=70}"
: "${HOOK_TIMEOUT_SEC:=60}"
: "${MEMORY_LOG_INTERVAL_SEC:=300}"
# Where the memory log reads smaps_rollup from; tests point it at a fake tree.
: "${AGENT_PROC_ROOT:=/proc}"
: "${SECRET_RETRY_SEC:=60}"
# How often a type with no job or image yet is checked again.
: "${RUNNER_WAIT_SEC:=300}"
: "${RESTART_MIN_SEC:=5}"
: "${RESTART_MAX_SEC:=60}"
# A run this long counts as healthy, so the next restart starts from the minimum.
: "${RESTART_RESET_SEC:=300}"
: "${TICK_SEC:=5}"
: "${REAPER_INTERVAL_SEC:=60}"
# A VM this much past its max run duration is deleted even if it's running
# (Compute Engine should have deleted it already).
: "${REAPER_GRACE_SEC:=300}"

# shellcheck source=hooks/gcp/lib.sh
source "$AGENT_ORCHESTRATOR_ROOT/hooks/gcp/lib.sh"
# The flags each orchestrator gets: one list, which the deploy checks against
# the image's own Claude (orchestrator-args.sh says why).
# shellcheck source=images/cloud/orchestrator/orchestrator-args.sh
source "$(dirname "${BASH_SOURCE[0]}")/orchestrator-args.sh"
readonly hooks_dir="$AGENT_ORCHESTRATOR_ROOT/hooks/gcp"

log() { printf 'supervisor: %s\n' "$*"; }

gcp_load_env || {
	log "no project.env in $AGENT_GCP_ENV_DIR"
	exit 1
}
: "${PROJECT_NUMBER:?}" "${PROJECT_ID:?}" "${REGION:?}"
mapfile -t types < <(gcp_types)
if ((${#types[@]} == 0)); then
	log "no runner types in $AGENT_GCP_ENV_DIR"
	exit 1
fi

# fetch_secret <token> <type> <file>: the type's Claude environment key, to an
# owner-only file. The value passes through a pipe, never a command line.
fetch_secret() {
	local token="$1" type="$2" file="$3"
	gcp_call "$token" GET "$GCP_SECRETS_URL/v1/projects/$PROJECT_NUMBER/secrets/claude-env-$type/versions/latest:access"
	if [[ "$GCP_STATUS" != 200 ]]; then
		log "$type: can't read secret claude-env-$type: HTTP $GCP_STATUS: $(gcp_error)"
		GCP_BODY=""
		return 1
	fi
	(
		umask 077
		jq -r '.payload.data' <<<"$GCP_BODY" | base64 -d >"$file.tmp"
	) && [[ -s "$file.tmp" ]] && mv "$file.tmp" "$file"
	local status=$?
	GCP_BODY=""
	((status == 0)) || log "$type: secret claude-env-$type came back empty or unreadable"
	return "$status"
}

prefix_lines() { sed -u "s/^/[$1] /"; }

# A type whose env file says DEPLOY=false has no job (deploy.sh builds its image
# but doesn't deploy it), so it gets no orchestrator: one would take sessions
# and fail to spawn every runner. A type whose env file gcp_check_type refuses is
# logged and skipped the same way: exiting here would put every type's
# orchestrator in a restart loop over one type's mistake (r3.f2).
deployed=()
for type in "${types[@]}"; do
	if ! reason="$(gcp_check_type "$type" 2>&1)"; then
		log "$reason; not starting its orchestrator"
	elif ! gcp_deploys "$type"; then
		log "$type: DEPLOY=false in its env file; not starting its orchestrator"
	else
		deployed+=("$type")
	fi
done
types=("${deployed[@]}")
if ((${#types[@]} == 0)); then
	log "no runner type left to start: each has DEPLOY=false or a refused env file"
	exit 1
fi

compute_types=()
for type in "${types[@]}"; do
	[[ "$(gcp_backend "$type")" == compute ]] && compute_types+=("$type")
done
if ((${#compute_types[@]} > 0)) && [[ -z "${ZONE:-}" ]]; then
	log "reaper: no ZONE in project.env; not reaping ${compute_types[*]}"
	compute_types=()
fi

# reap_type <token> <type>: one pass of the reaper over the type's VMs.
reap_type() {
	local token="$1" type="$2" max_run zone_url page="" instance name status created now age
	max_run="$(gcp_type_setting "$type" MAX_RUN_DURATION_SEC 34200)"
	zone_url="$GCP_COMPUTE_URL/compute/v1/projects/$PROJECT_ID/zones/$ZONE"
	now="$(date +%s)"
	while :; do
		gcp_call "$token" GET "$zone_url/instances?filter=$(gcp_uri "labels.agent-images-type=$type")${page:+&pageToken=$(gcp_uri "$page")}"
		if [[ "$GCP_STATUS" != 200 ]]; then
			log "reaper: $type: listing VMs: HTTP $GCP_STATUS: $(gcp_error)"
			return
		fi
		local body="$GCP_BODY"
		while read -r instance; do
			# The deploy's smoke VMs are its own to read and delete: one powers
			# off once its result is in, and deleting it then could beat the
			# deploy to the result.
			[[ "$(jq -r '.labels["agent-images-role"] // empty' <<<"$instance")" != smoke ]] || continue
			name="$(jq -r '.name' <<<"$instance")"
			status="$(jq -r '.status' <<<"$instance")"
			created="$(date -d "$(jq -r '.creationTimestamp' <<<"$instance")" +%s 2>/dev/null || echo "$now")"
			age=$((now - created))
			case "$status" in
			TERMINATED | STOPPED | SUSPENDED) reap_delete "$token" "$zone_url" "$type" "$name" "$status" ;;
			*)
				if ((age > max_run + REAPER_GRACE_SEC)); then
					reap_delete "$token" "$zone_url" "$type" "$name" "$status, ${age}s old"
				elif jq -e '.metadata.items // [] | any(.key == "work-order")' <<<"$instance" >/dev/null; then
					reap_order "$token" "$zone_url" "$type" "$instance"
				fi
				;;
			esac
		done < <(jq -c '.items // [] | .[]' <<<"$body")
		page="$(jq -r '.nextPageToken // empty' <<<"$body")"
		[[ -n "$page" ]] || break
	done
}

# reap_delete <token> <zone url> <type> <name> <why>: delete, without waiting.
reap_delete() {
	gcp_call "$1" DELETE "$2/instances/$4"
	case "$GCP_STATUS" in
	2*) log "reaper: $3: deleting $4 ($5)" ;;
	404) ;;
	*) log "reaper: $3: deleting $4: HTTP $GCP_STATUS: $(gcp_error)" ;;
	esac
}

# reap_order <token> <zone url> <type> <instance json>: drop the work-order key
# once the guest has read it. The fingerprint makes a concurrent change fail
# rather than be overwritten; the next pass tries again.
reap_order() {
	local token="$1" zone_url="$2" type="$3" instance="$4" name state
	name="$(jq -r '.name' <<<"$instance")"
	gcp_call "$token" GET "$zone_url/instances/$name/getGuestAttributes?queryPath=$(gcp_uri agent-images/order)"
	[[ "$GCP_STATUS" == 200 ]] || return 0
	state="$(jq -r '.queryValue.items // [] | map(select(.key == "order")) | .[0].value // empty' <<<"$GCP_BODY" | tr -d '[:space:]')"
	[[ "$state" == read ]] || return 0
	gcp_call "$token" POST "$zone_url/instances/$name/setMetadata" - < <(
		jq -c '{fingerprint: .metadata.fingerprint, items: [.metadata.items[] | select(.key != "work-order")]}' <<<"$instance"
	)
	if [[ "$GCP_STATUS" == 2* ]]; then
		log "reaper: $type: removed the spent work order from $name"
	else
		log "reaper: $type: removing the work order from $name: HTTP $GCP_STATUS: $(gcp_error)"
	fi
}

# reap: one pass over every compute type; runs in the background.
reap() {
	local token type
	token="$(gcp_token)" || {
		log "reaper: no access token from the metadata server"
		return 0
	}
	for type in "${compute_types[@]}"; do
		reap_type "$token" "$type"
	done
}

declare -A pid_of=() type_of=() started_at=() next_start=() backoff=() port_of=()
index=0
for type in "${types[@]}"; do
	port_of[$type]=$((HEALTH_PORT_BASE + index))
	next_start[$type]=0
	backoff[$type]=$RESTART_MIN_SEC
	index=$((index + 1))
done

mkdir -p "$SUPERVISOR_SECRETS_DIR"
chmod 700 "$SUPERVISOR_SECRETS_DIR"

# runner_ready <token> <type>: whether the type has something to spawn: its
# Cloud Run job, or an image in its family. Only a 404 says no: on any other
# answer it can't tell, logs why and says yes, and the hook reports a real
# problem per session as before. "Not yet" is logged once, and its end once.
declare -A waiting=()
runner_ready() {
	local token="$1" type="$2" what url
	if [[ "$(gcp_backend "$type")" == compute ]]; then
		what="image in family $(gcp_image_family "$type")"
		url="$GCP_COMPUTE_URL/compute/v1/projects/$PROJECT_ID/global/images/family/$(gcp_image_family "$type")"
	else
		what="Cloud Run job agent-$type"
		url="$GCP_RUN_URL/v2/projects/$PROJECT_ID/locations/$REGION/jobs/agent-$type"
	fi
	gcp_call "$token" GET "$url"
	case "$GCP_STATUS" in
	200)
		[[ -z "${waiting[$type]:-}" ]] || log "$type: its $what is there now"
		unset "waiting[$type]"
		;;
	404)
		[[ -n "${waiting[$type]:-}" ]] ||
			log "$type: no $what yet; not starting its orchestrator until there is (checking every ${RUNNER_WAIT_SEC}s)"
		waiting[$type]=1
		return 1
		;;
	*) log "$type: can't check for its $what (HTTP $GCP_STATUS: $(gcp_error)); starting its orchestrator anyway" ;;
	esac
}

# start_type <type> <now>: start its orchestrator, or schedule another try.
# Its API calls can each take up to GCP_CURL_MAX_TIME, and a SIGTERM's trap runs
# only once the call in progress returns, so it checks $stopping after them: an
# orchestrator started after the stop began would miss the drain the host's
# docker stop allows. One token serves both calls.
start_type() {
	local type="$1" now="$2" secret_file="$SUPERVISOR_SECRETS_DIR/$1" pid token
	if ! token="$(gcp_token)"; then
		log "$type: no access token from the metadata server"
		next_start[$type]=$((now + SECRET_RETRY_SEC))
		return
	fi
	if ! runner_ready "$token" "$type"; then
		next_start[$type]=$((now + RUNNER_WAIT_SEC))
		return
	fi
	$stopping && return
	if ! fetch_secret "$token" "$type" "$secret_file"; then
		next_start[$type]=$((now + SECRET_RETRY_SEC))
		return
	fi
	$stopping && return
	local min_idle expected
	min_idle="$(gcp_type_setting "$type" MIN_IDLE 0)"
	expected="$(gcp_type_setting "$type" EXPECTED_SPAWN_SECONDS 120)"
	orchestrator_args "$hooks_dir" "$secret_file" "$min_idle" "$expected" \
		"${port_of[$type]}" "$HOOK_TIMEOUT_SEC" "$SHUTDOWN_DRAIN_SEC"
	# The subshell becomes the orchestrator at exec, so $! is its pid: signals
	# reach it directly, and the log prefixer is its child, not ours.
	(
		exec > >(prefix_lines "$type") 2>&1
		exec env RUNNER_TYPE="$type" "$CLAUDE_BIN" self-hosted-runner orchestrator "${ORCHESTRATOR_ARGS[@]}"
	) &
	pid=$!
	pid_of[$type]=$pid
	type_of[$pid]=$type
	started_at[$type]=$now
	log "$type: started orchestrator (pid $pid, health port ${port_of[$type]}, min-idle $min_idle)"
}

# tree_pids <pid>: the pid and every descendant, one per line.
tree_pids() {
	ps -e -o pid=,ppid= | awk -v root="$1" '
		{ parent[$1] = $2 }
		END {
			for (pid in parent) {
				for (p = pid; p != "" && p > 1; p = parent[p]) {
					if (p == root) { print pid; break }
				}
			}
		}'
}

# tree_memory <pid>: "<pss_kib> <rss_kib> <processes>" for the pid and its children.
# A process can exit between listing it and reading it; its smaps_rollup then fails
# to open, and it is skipped rather than failing the function (set -e).
tree_memory() {
	local pss=0 rss=0 count=0 pid key value _ p_pss p_rss
	while read -r pid; do
		p_pss=0 p_rss=0
		{
			while read -r key value _; do
				case "$key" in
				Pss:) p_pss=$((p_pss + value)) ;;
				Rss:) p_rss=$((p_rss + value)) ;;
				esac
			done <"$AGENT_PROC_ROOT/$pid/smaps_rollup"
		} 2>/dev/null || continue
		count=$((count + 1))
		pss=$((pss + p_pss))
		rss=$((rss + p_rss))
	done < <(tree_pids "$1")
	printf '%s %s %s\n' "$pss" "$rss" "$count"
}

log_memory() {
	local type pid pss rss count connected total=0 self available mem_total
	for type in "${types[@]}"; do
		pid="${pid_of[$type]:-}"
		if [[ -z "$pid" ]]; then
			log "memory type=$type running=false"
			continue
		fi
		# Memory logging must never stop the supervisor: log zeros if there is no answer.
		read -r pss rss count < <(tree_memory "$pid") || { pss=0 rss=0 count=0; }
		connected="$(curl -s --max-time 2 "http://127.0.0.1:${port_of[$type]}/healthz" 2>/dev/null |
			jq -r '.connected // "unknown"' 2>/dev/null)" || connected=""
		log "memory type=$type pid=$pid processes=$count pss_kib=$pss rss_kib=$rss connected=${connected:-unknown}"
		total=$((total + pss))
	done
	self="$(awk '$1 == "Pss:" { print $2 }' "$AGENT_PROC_ROOT/$$/smaps_rollup" 2>/dev/null)" || self=""
	available="$(awk '$1 == "MemAvailable:" { print $2 }' /proc/meminfo)"
	mem_total="$(awk '$1 == "MemTotal:" { print $2 }' /proc/meminfo)"
	log "memory total orchestrators_pss_kib=$total supervisor_pss_kib=${self:-0} mem_available_kib=$available mem_total_kib=$mem_total"
}

# Each signal starts a round, and every orchestrator gets SIGTERM once per round:
# from the trap for those already running, and from the main loop for one whose
# start the signal interrupted (the trap can run between its fork and the line
# that records its pid). A second signal is a second round, which the
# orchestrator reads as "stop the hooks at once".
stopping=false
signal_round=0
declare -A signaled=()
forward_signal() {
	local type pid
	for type in "${types[@]}"; do
		pid="${pid_of[$type]:-}"
		[[ -n "$pid" && "${signaled[$pid]:-}" != "$signal_round" ]] || continue
		signaled[$pid]=$signal_round
		kill -TERM "$pid" 2>/dev/null || true
	done
}
on_signal() {
	if ! $stopping; then
		log "stopping: passing SIGTERM to every orchestrator (they drain for up to ${SHUTDOWN_DRAIN_SEC}s)"
	fi
	stopping=true
	signal_round=$((signal_round + 1))
	forward_signal
}
trap on_signal TERM INT

# child_exited <type> <pid> <status>: forget it, and schedule its restart.
child_exited() {
	local type="$1" pid="$2" status="$3" now
	unset "type_of[$pid]"
	pid_of[$type]=""
	if $stopping; then
		log "$type: orchestrator stopped ($status)"
		return
	fi
	now="$(date +%s)"
	if ((now - started_at[$type] >= RESTART_RESET_SEC)); then
		backoff[$type]=$RESTART_MIN_SEC
	fi
	log "$type: orchestrator exited ($status) after $((now - started_at[$type]))s; restarting in ${backoff[$type]}s"
	next_start[$type]=$((now + backoff[$type]))
	backoff[$type]=$((backoff[$type] * 2 > RESTART_MAX_SEC ? RESTART_MAX_SEC : backoff[$type] * 2))
}

# Insurance alongside wait -n: every pass also collects any recorded child that
# is no longer running, so an exit wait -n didn't report (several orchestrators
# ending together on SIGTERM) can't leave the supervisor waiting forever.
reap_exited() {
	local type pid status
	for type in "${types[@]}"; do
		pid="${pid_of[$type]:-}"
		if [[ -z "$pid" ]] || kill -0 "$pid" 2>/dev/null; then
			continue
		fi
		status=0
		wait "$pid" 2>/dev/null || status=$?
		child_exited "$type" "$pid" "$status"
	done
}

reaper_pid=""
start_reaper() {
	reap &
	reaper_pid=$!
}

tick_pid=""
start_tick() {
	sleep "$TICK_SEC" &
	tick_pid=$!
}

running_count() {
	local pid count=0
	for pid in "${pid_of[@]}"; do
		[[ -n "$pid" ]] && count=$((count + 1))
	done
	printf '%s' "$count"
}

log "runner types: ${types[*]}${compute_types[*]:+ (VMs reaped: ${compute_types[*]})}"
start_tick
next_memory=$(($(date +%s) + MEMORY_LOG_INTERVAL_SEC))
next_reap=0

while :; do
	reap_exited
	now="$(date +%s)"
	if ! $stopping; then
		for type in "${types[@]}"; do
			# A signal that arrived during the last type's start ends the round.
			$stopping && break
			if [[ -z "${pid_of[$type]:-}" ]] && ((now >= next_start[$type])); then
				start_type "$type" "$now"
			fi
		done
		if ((now >= next_memory)); then
			log_memory
			next_memory=$((now + MEMORY_LOG_INTERVAL_SEC))
		fi
		if ((${#compute_types[@]} > 0)) && [[ -z "$reaper_pid" ]] && ((now >= next_reap)); then
			start_reaper
			next_reap=$((now + REAPER_INTERVAL_SEC))
		fi
	else
		forward_signal
		if (($(running_count) == 0)); then
			kill "$tick_pid" ${reaper_pid:+"$reaper_pid"} 2>/dev/null || true
			log "every orchestrator has stopped"
			exit 0
		fi
	fi

	exited=""
	status=0
	# Interrupted by a trapped signal, wait returns and unsets exited; loop again.
	wait -n -p exited || status=$?
	[[ -n "${exited:-}" ]] || continue
	if [[ "$exited" == "$tick_pid" ]]; then
		start_tick
		continue
	fi
	if [[ "$exited" == "$reaper_pid" ]]; then
		reaper_pid=""
		continue
	fi
	type="${type_of[$exited]:-}"
	[[ -n "$type" ]] || continue
	child_exited "$type" "$exited" "$status"
done
