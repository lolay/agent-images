#!/bin/bash
# Claude Code Stop hook. The runner seeds ~/.claude into every session, so this
# runs at the end of each turn. When the project has uncommitted or unpushed
# work, it asks Claude (once per turn) to commit and push, so an early session
# end (the watchdog, a host restart, an idle release) costs at most one turn.
#
# stdin: the hook's JSON payload. stdout: {"decision":"block",...} to ask, or
# nothing to let Claude stop.
set -uo pipefail

input="$(cat)"
# After a block the harness calls the hook again with stop_hook_active=true;
# asking again would loop.
if jq -e '.stop_hook_active == true' >/dev/null 2>&1 <<<"$input"; then
	exit 0
fi

readonly project="${CLAUDE_PROJECT_DIR:-$PWD}"
git_in() { git -C "$project" "$@" 2>/dev/null; }

block() {
	jq -cn --arg reason "$1" '{decision: "block", reason: $reason}'
	exit 0
}

git_in rev-parse --git-dir >/dev/null || exit 0
[[ -n "$(git_in remote)" ]] || exit 0

# .claude/ holds runner-seeded settings and CLI state, not the session's work.
if [[ -n "$(git_in status --porcelain -- . ':(exclude).claude/')" ]]; then
	block "There are uncommitted changes. Commit them and push the branch before you stop."
fi

# Unpushed means on HEAD but not on any remote-tracking ref or FETCH_HEAD (the
# runner's checkout fetches without creating origin/* refs). With neither to
# compare against, say nothing rather than flag every commit.
base=(--remotes)
has_base=false
if git_in rev-parse --verify -q FETCH_HEAD >/dev/null; then
	base+=(FETCH_HEAD)
	has_base=true
fi
[[ -n "$(git_in for-each-ref --count=1 refs/remotes)" ]] && has_base=true
$has_base || exit 0

unpushed="$(git_in rev-list --count HEAD --not "${base[@]}")" || exit 0
if ((unpushed > 0)); then
	if branch="$(git_in symbolic-ref --short -q HEAD)"; then
		block "There are $unpushed unpushed commit(s) on $branch. Push the branch before you stop."
	fi
	block "There are $unpushed unpushed commit(s) on a detached HEAD. Create a branch and push it before you stop."
fi
