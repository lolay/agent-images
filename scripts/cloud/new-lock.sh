#!/bin/bash
# A new runner type's first lock, for cloud-build.yml (specs/gcp.md, "The lock";
# r4.f11, option C):
#
#   new-lock.sh <type> <dir>    nothing, when <type> has a lock: a lock that
#                               exists is the watch's to move, never this
#                               script's. Otherwise resolve one into
#                               <dir>/versions.lock (lock.sh resolve), name the
#                               artifact for the workflow to upload, put the
#                               command that fetches it in the job summary, and
#                               fail: the author commits the lock and reruns
#
# The lock is only resolved here, never committed: the pull request's author
# commits it at images/cloud/<type>/versions.lock, so lock.sh stays the lock's one
# writer and no workflow writes the branch. Needs what lock.sh resolve needs
# (Linux, Docker, Homebrew) and GitHub's step variables: GITHUB_OUTPUT (gets
# artifact=<name>), GITHUB_STEP_SUMMARY, GITHUB_RUN_ID and GITHUB_REPOSITORY.
# Run from the repo root.
set -euo pipefail
shopt -s inherit_errexit

lock_sh="$(dirname "${BASH_SOURCE[0]}")/lock.sh"
readonly lock_sh

die() {
	printf 'new-lock: %s\n' "$*" >&2
	exit 1
}

(($# == 2)) || die "usage: new-lock.sh <type> <dir>"
type="$1" dir="$2"

lock="$("$lock_sh" path "$type")"
if [[ -e "$lock" ]]; then
	printf '%s has a lock (%s): nothing to resolve\n' "$type" "$lock"
	exit 0
fi

for name in GITHUB_OUTPUT GITHUB_STEP_SUMMARY GITHUB_RUN_ID GITHUB_REPOSITORY; do
	[[ -n "${!name:-}" ]] || die "$name isn't set (this runs in cloud-build.yml)"
done

mkdir -p "$dir"
"$lock_sh" resolve "$type" >"$dir/versions.lock" || die "couldn't resolve a lock for $type"

artifact="versions-lock-$type"
printf 'artifact=%s\n' "$artifact" >>"$GITHUB_OUTPUT"
download="gh run download $GITHUB_RUN_ID --repo $GITHUB_REPOSITORY --name $artifact --dir ${lock%/versions.lock}"
# shellcheck disable=SC2016 # markdown backticks
{
	printf '## %s has no lock\n\n' "$type"
	printf 'A new runner type builds from a lock that is committed with it. This run resolved one\n'
	printf 'from upstream (`scripts/cloud/lock.sh resolve %s`); commit it on this branch and\n' "$type"
	printf 'the build reruns from it:\n\n'
	printf '```bash\n%s\ngit add %s\n```\n\n' "$download" "$lock"
	printf '<details><summary>The resolved lock</summary>\n\n```text\n'
	cat "$dir/versions.lock"
	printf '```\n\n</details>\n'
} >>"$GITHUB_STEP_SUMMARY"

cat "$dir/versions.lock"
printf '::error title=%s has no lock::Commit the lock this run resolved at %s (artifact %s): %s\n' \
	"$type" "$lock" "$artifact" "$download"
exit 1
