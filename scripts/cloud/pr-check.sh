#!/bin/bash
# The pull request check's two decisions (.github/workflows/cloud-build.yml;
# specs/gcp.md, "Only main deploys"), here so they're tested: main's ruleset
# and the lock bot's auto-merge rest on `gate`.
#
#   pr-check.sh relevant              the changed paths on stdin, one a line:
#                                     print true when any is something the check
#                                     builds, smokes or tests (a cloud image's
#                                     inputs, a lock, the tests, any workflow),
#                                     else false
#   pr-check.sh gate <changes> <relevant> <check> <build>
#                                     the jobs' results (success, failure,
#                                     cancelled, skipped) and `changes`' output:
#                                     succeed when nothing the check covers
#                                     changed, or when check and every build leg
#                                     passed; fail, saying why, on anything else
set -euo pipefail

# What the check covers: the paths that build, smoke or test a cloud image
# (images/cloud/<type>/versions.lock included: a lock pull request must build),
# every workflow (the unit tests read them all, test_workflows.py's pipefail
# guard among them), CI's tool versions and the Renovate config that bumps
# them, and Anthropic's setup script (it installs linux's toolsets).
readonly relevant_paths='^(images/(cloud|android|shared|toolsets)/|hooks/gcp/|infra/gcp/|scripts/(cloud|ci|anthropic)/|tests/unit/|\.dockerignore$|Makefile$|triage\.yaml$|\.github/(workflows/[^/]*\.yml|renovate\.json)$)'

usage() {
	printf 'usage: pr-check.sh relevant < paths | gate <changes> <relevant> <check> <build>\n' >&2
	exit 2
}

cmd_relevant() {
	local paths
	paths="$(cat)"
	if grep -qE "$relevant_paths" <<<"$paths"; then
		printf 'true\n'
	else
		printf 'false\n'
	fi
}

cmd_gate() {
	local changes="$1" relevant="$2" check="$3" build="$4"
	printf 'changes=%s relevant=%s check=%s build=%s\n' "$changes" "$relevant" "$check" "$build"
	if [[ "$changes" != success ]]; then
		printf '::error::changes: %s\n' "$changes"
		return 1
	fi
	case "$relevant" in
	false)
		printf 'Nothing cloud-build checks changed.\n'
		return 0
		;;
	true) ;;
	*)
		printf '::error::changes gave relevant=%s, not true or false\n' "$relevant"
		return 1
		;;
	esac
	if [[ "$check" != success || "$build" != success ]]; then
		printf '::error::check: %s, build: %s\n' "$check" "$build"
		return 1
	fi
}

case "${1:-}" in
relevant)
	(($# == 1)) || usage
	cmd_relevant
	;;
gate)
	(($# == 5)) || usage
	cmd_gate "$2" "$3" "$4" "$5"
	;;
*) usage ;;
esac
