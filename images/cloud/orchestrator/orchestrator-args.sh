# shellcheck shell=bash
# The one list of flags the supervisor passes to `claude self-hosted-runner
# orchestrator` (r1.f10). Sourced by:
#
#   - supervisor.sh, which starts each type's orchestrator with them;
#   - scripts/cloud/check-orchestrator-flags.sh, which the deploy runs against
#     the pushed image's own `--help` before pointing the host at it: a Claude
#     release that renamed or dropped one would make every orchestrator exit at
#     start, restarted forever, with no check failing;
#   - the unit tests' fake claude (tests/unit/test_supervisor.py), which
#     refuses any other flag, and one without its value, as the real CLI does.
#
# Beside supervisor.sh in the repo and in the image, so it's found from there.

# orchestrator_args <hooks dir> <secret file> <min idle> <expected spawn sec>
#     <health port> <hook timeout sec> <shutdown drain sec>
# Sets the array ORCHESTRATOR_ARGS: each flag followed by its value.
orchestrator_args() {
	if (($# != 7)); then
		printf 'orchestrator_args: takes 7 values, got %s\n' "$#" >&2
		return 1
	fi
	ORCHESTRATOR_ARGS=(
		--hooks-dir "$1"
		--environment-secret-file "$2"
		--min-idle "$3"
		--expected-spawn-seconds "$4"
		--health-port "$5"
		--hook-timeout "$6"
		--shutdown-drain-sec "$7"
	)
}

# orchestrator_flags: the flags orchestrator_args passes, one per line.
orchestrator_flags() {
	local ORCHESTRATOR_ARGS i
	orchestrator_args 1 2 3 4 5 6 7
	for ((i = 0; i < ${#ORCHESTRATOR_ARGS[@]}; i += 2)); do
		printf '%s\n' "${ORCHESTRATOR_ARGS[i]}"
	done
}
