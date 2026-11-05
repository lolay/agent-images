#!/bin/bash
# Claude Code SessionStart hook for the Cloud Run images (specs/gcp.md,
# "Storage" and "Session notice"). The runner seeds ~/.claude into every session,
# so this runs when each session starts or resumes. It:
#   1. gives the session its own folder in the outputs bucket and exports it as
#      AGENT_OUTPUTS through $CLAUDE_ENV_FILE (a standby starts before it has a
#      session, so the entrypoint can't);
#   2. prints the session notice: when the session ends and, once the deploy
#      sets AGENT_COST_USD_PER_HOUR, what the runtime costs;
#   3. adds a line of storage facts to the session's context.
#
# stdin: the hook's JSON payload. stdout: JSON with systemMessage (shown to the
# user) and additionalContext (given to Claude). It never fails the session: a
# problem becomes a warning in the notice.
set -uo pipefail

: "${AGENT_SCRATCH:=/scratch}"
: "${AGENT_OUTPUTS_ROOT:=/mnt/outputs}"
: "${AGENT_DATASETS:=/mnt/datasets}"
: "${AGENT_TYPE:=unknown}"
: "${AGENT_NOTICE_TZ:=UTC}"
: "${CLAUDE_RELEASE_IDLE_SESSION_MIN=60}"
: "${CLAUDE_KILL_SESSION_AFTER_MIN=480}"
# Tests pin the clock.
now="${AGENT_NOW:-$(date +%s)}"

input="$(cat)"
warnings=()

session_id="${CLAUDE_CODE_REMOTE_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}"
id_source="CLAUDE_CODE_REMOTE_SESSION_ID"
[[ -n "${CLAUDE_CODE_REMOTE_SESSION_ID:-}" ]] || id_source="CLAUDE_CODE_SESSION_ID"
if [[ -z "$session_id" ]]; then
	session_id="$(jq -r '.session_id // empty' <<<"$input" 2>/dev/null)"
	id_source="the hook payload"
fi
# A path component, nothing else.
session_id="${session_id//[^A-Za-z0-9_.-]/_}"

# A resumed session keeps the folder it started with, whatever the date now.
outputs=""
if [[ -n "$session_id" ]]; then
	shopt -s nullglob
	existing=("$AGENT_OUTPUTS_ROOT/$AGENT_TYPE"/*/"$session_id")
	shopt -u nullglob
	if ((${#existing[@]} > 0)); then
		outputs="${existing[0]}"
	else
		outputs="$AGENT_OUTPUTS_ROOT/$AGENT_TYPE/$(date -u -d "@$now" +%F)/$session_id"
	fi
	if ! mkdir -p "$outputs" 2>/dev/null; then
		warnings+=("couldn't create $outputs; is the outputs bucket mounted?")
		outputs=""
	fi
else
	warnings+=("no session id, so no outputs folder")
fi
if [[ -n "$outputs" && -n "${CLAUDE_ENV_FILE:-}" ]]; then
	printf 'export AGENT_OUTPUTS=%q\n' "$outputs" >>"$CLAUDE_ENV_FILE"
fi

duration() {
	local minutes=$(($1 / 60))
	if ((minutes >= 60)); then
		printf '%d h %d min' $((minutes / 60)) $((minutes % 60))
	else
		printf '%d min' "$minutes"
	fi
}

notice=()
if [[ -n "${AGENT_COST_USD_PER_HOUR:-}" ]]; then
	notice+=("⚠ This runtime costs approximately \$${AGENT_COST_USD_PER_HOUR}/hour.")
fi
end=""
[[ -n "$CLAUDE_KILL_SESSION_AFTER_MIN" ]] && end=$((now + CLAUDE_KILL_SESSION_AFTER_MIN * 60))
if [[ -n "${AGENT_RETIRE_AT:-}" ]] && { [[ -z "$end" ]] || ((AGENT_RETIRE_AT < end)); }; then
	end="$AGENT_RETIRE_AT"
fi
if [[ -n "$end" ]]; then
	at="$(TZ="$AGENT_NOTICE_TZ" date -d "@$end" '+%-I:%M %p %Z')"
	ends="ends by $at at the latest (in $(duration $((end - now))))"
	if [[ -n "$CLAUDE_RELEASE_IDLE_SESSION_MIN" ]]; then
		notice+=("⚠ This session will time out after $CLAUDE_RELEASE_IDLE_SESSION_MIN minutes of inactivity, and $ends. Push your work before then.")
	else
		notice+=("⚠ This session $ends. Push your work before then.")
	fi
elif [[ -n "$CLAUDE_RELEASE_IDLE_SESSION_MIN" ]]; then
	notice+=("⚠ This session will time out after $CLAUDE_RELEASE_IDLE_SESSION_MIN minutes of inactivity. Push your work before then.")
fi

scratch_free="$(df -h --output=avail "$AGENT_SCRATCH" 2>/dev/null | tail -n 1 | tr -d ' ')"
facts="Storage: scratch $AGENT_SCRATCH (${scratch_free:-unknown} free)"
if [[ -n "$outputs" ]]; then
	gs_path="${outputs#"$AGENT_OUTPUTS_ROOT"/}"
	facts+="; outputs \$AGENT_OUTPUTS=$outputs"
	[[ -n "${AGENT_OUTPUTS_BUCKET:-}" ]] && facts+=" (gs://$AGENT_OUTPUTS_BUCKET/$gs_path)"
fi
# FUSE lists over the network; don't let a slow bucket hold the session up.
datasets_top="$(timeout 5 ls -1 "$AGENT_DATASETS" 2>/dev/null | head -n 8 | paste -sd, -)"
facts+="; datasets \$AGENT_DATASETS=$AGENT_DATASETS (${datasets_top:-empty}, read-only)."
facts+=" Session id from $id_source."

message="$(printf '%s\n' "${notice[@]}" "${warnings[@]/#/⚠ agent-images: }")"
message="${message%$'\n'}"
context="$facts"
if ((${#notice[@]} > 0)); then
	context+=$'\n'"Session notice (start your first reply with these lines):"$'\n'"$(printf '%s\n' "${notice[@]}")"
fi

jq -cn --arg message "$message" --arg context "$context" '
	{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $context}}
	+ (if $message == "" then {} else {systemMessage: $message} end)'
