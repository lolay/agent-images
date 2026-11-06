#!/bin/bash
# The lock watch's steps (specs/gcp.md, "Watch" and "The lock bot"), for
# .github/workflows/lock-watch.yml. The lock itself is read and written only by
# lock.sh; this script decides what to propose and proposes it.
#
#   lock-watch.sh types <class>               the Cloud Run types a run resolves:
#                                             nightly, weekly or all (D4)
#   lock-watch.sh changed <base> <candidate>  the lock paths that differ, one a line
#   lock-watch.sh carry <class> <candidate> <main ref> <branch ref>
#                                             copy into <candidate> the locks the
#                                             open pull request's branch changed for
#                                             types this run didn't resolve, so one
#                                             run never drops another's candidate
#   lock-watch.sh proposed <candidate> <main ref> <branch ref>
#                                             succeed when the branch is already
#                                             <main ref> plus exactly <candidate>
#   lock-watch.sh guard                       fail unless every path on stdin is a
#                                             lock (images/cloud/<type>/versions.lock)
#   lock-watch.sh title <base> <candidate>    the pull request's title
#   lock-watch.sh body <base> <candidate>     its description: what moved, per type
#   lock-watch.sh pins <tree>                 markdown: each pin, upstream's newest
#                                             and its issue's state (GH_TOKEN reads it)
#   lock-watch.sh token                       print an installation token for the
#                                             lock bot, narrowed to contents and pull
#                                             requests on this repo (LOCK_BOT_APP_ID,
#                                             LOCK_BOT_PRIVATE_KEY)
#   lock-watch.sh revoke                      revoke the token in GH_TOKEN
#   lock-watch.sh propose <candidate> <title> <body file>
#                                             commit the candidate's locks onto
#                                             main's head as lock/watch, open or
#                                             refresh the pull request, auto-merge on
#   lock-watch.sh close                       close the open lock pull request
#
# <base> and <candidate> are trees laid out like the repo (images/cloud/<type>/
# versions.lock). GITHUB_REPOSITORY names the repo, GITHUB_API_URL the API. Run
# from the repo root.
set -euo pipefail
shopt -s inherit_errexit

# shellcheck source=scripts/cloud/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

lock_sh="$(dirname "${BASH_SOURCE[0]}")/lock.sh"
readonly lock_sh
readonly branch=lock/watch
readonly staging=lock/watch-staging
readonly lock_pattern='^images/cloud/[a-z0-9-]+/versions\.lock$'
readonly api="${GITHUB_API_URL:-https://api.github.com}"
# GitHub's limit is 256; a longer title is cut.
readonly title_max=256

die() {
	printf 'lock-watch: %s\n' "$*" >&2
	exit 1
}
usage() { die "usage: lock-watch.sh types|changed|carry|proposed|guard|title|body|pins|token|revoke|propose|close ..."; }

repo() {
	[[ "${GITHUB_REPOSITORY:-}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "GITHUB_REPOSITORY isn't owner/name"
	printf '%s' "$GITHUB_REPOSITORY"
}

cmd_types() {
	local class="$1" type
	[[ "$class" =~ ^(nightly|weekly|all)$ ]] || die "class '$class': nightly, weekly or all"
	while read -r type; do
		[[ "$(gcp_backend "$type")" == run ]] || continue
		if [[ "$class" == all || "$(gcp_rebuild_class "$type")" == "$class" ]]; then
			printf '%s\n' "$type"
		fi
	done < <(gcp_types)
}

# files_in <tree>: every path under a tree that isn't a directory, relative to
# it, sorted: what propose would commit, so what the guard checks.
files_in() {
	(cd "$1" && find . -mindepth 1 -not -type d | sed 's|^\./||') | LC_ALL=C sort
}

# locks_in <tree>: the lock paths under a tree, relative to it, sorted.
locks_in() {
	(cd "$1" && find images/cloud -mindepth 2 -maxdepth 2 -name versions.lock 2>/dev/null) | LC_ALL=C sort
}

cmd_changed() {
	local path
	while read -r path; do
		cmp -s "$1/$path" "$2/$path" || printf '%s\n' "$path"
	done < <(locks_in "$2")
}

cmd_carry() {
	local class="$1" candidate="$2" main="$3" from="$4" path type resolved moved
	resolved=" $(cmd_types "$class" | tr '\n' ' ')"
	# Three dots: what the branch changed since it left main, not what main
	# changed since.
	moved="$(git diff --name-only "$main...$from")"
	while read -r path; do
		[[ "$path" =~ $lock_pattern ]] || continue
		type="${path#images/cloud/}"
		type="${type%%/*}"
		[[ "$resolved" != *" $type "* ]] || continue
		mkdir -p "$candidate/${path%/*}"
		git show "$from:$path" >"$candidate/$path"
		printf 'carried %s from %s\n' "$path" "$from" >&2
	done <<<"$moved"
}

cmd_proposed() {
	local candidate="$1" main="$2" from="$3" path parent
	parent="$(git rev-parse --verify --quiet "$from^")" || return 1
	[[ "$parent" == "$(git rev-parse "$main")" ]] || return 1
	while read -r path; do
		cmp -s "$candidate/$path" <(git show "$from:$path" 2>/dev/null) || return 1
	done < <(locks_in "$candidate")
}

cmd_guard() {
	local path bad=0 count=0
	while IFS= read -r path; do
		[[ -n "$path" ]] || continue
		count=$((count + 1))
		if [[ ! "$path" =~ $lock_pattern ]]; then
			printf 'lock-watch: refusing to push %s: the watch writes only images/cloud/<type>/versions.lock\n' "$path" >&2
			bad=1
		fi
	done
	((bad == 0)) || exit 1
	((count > 0)) || die "nothing to push"
}

type_of() {
	local type="${1#images/cloud/}"
	printf '%s' "${type%%/*}"
}

# summary_of <base> <candidate> <path>: lock.sh's summary, or a fallback when
# only the snapshot or a pin moved.
summary_of() {
	local old="$1/$3" summary
	[[ -f "$old" ]] || old=/dev/null
	summary="$("$lock_sh" summary "$old" "$2/$3")"
	printf '%s' "${summary:-pins and snapshot}"
}

cmd_title() {
	local base="$1" candidate="$2" paths path types=() parts=() title joined
	mapfile -t paths < <(cmd_changed "$base" "$candidate")
	((${#paths[@]} > 0)) || die "no lock moved"
	for path in "${paths[@]}"; do
		types+=("$(type_of "$path")")
	done
	if ((${#paths[@]} == 1)); then
		title="lock(${types[0]}): $(summary_of "$base" "$candidate" "${paths[0]}")"
	else
		for path in "${paths[@]}"; do
			parts+=("$(type_of "$path"): $(summary_of "$base" "$candidate" "$path")")
		done
		joined="$(printf '%s, ' "${types[@]}")"
		title="lock(${joined%, }): "
		joined="$(printf '%s; ' "${parts[@]}")"
		title+="${joined%; }"
	fi
	if ((${#title} > title_max)); then
		title="${title:0:title_max-3}..."
	fi
	printf '%s\n' "$title"
}

cmd_body() {
	local base="$1" candidate="$2" path old
	printf 'Candidate locks from the lock watch'
	if [[ -n "${GITHUB_RUN_ID:-}" ]]; then
		printf ' ([run](%s/%s/actions/runs/%s))' "${GITHUB_SERVER_URL:-https://github.com}" "$(repo)" "$GITHUB_RUN_ID"
	fi
	# shellcheck disable=SC2016 # markdown backticks, not expansions
	printf '. It merges itself when `cloud-build`'"'"'s `gate` passes; a red check holds it, and the next run refreshes it (specs/gcp.md, "Watch").\n'
	while read -r path; do
		old="$base/$path"
		[[ -f "$old" ]] || old=/dev/null
		printf '\n### %s\n\n```text\n' "$(type_of "$path")"
		"$lock_sh" diff "$old" "$candidate/$path"
		printf '```\n'
	done < <(cmd_changed "$base" "$candidate")
}

# issue_state <url>: the state of a GitHub issue or pull request, read with
# GH_TOKEN; a word for anything else.
issue_state() {
	local state
	if [[ "$1" == - ]]; then
		printf 'no issue named'
	elif [[ "$1" =~ ^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(issues|pull)/([0-9]+)$ ]]; then
		if state="$(gh api "repos/${BASH_REMATCH[1]}/${BASH_REMATCH[2]}/issues/${BASH_REMATCH[4]}" --jq '.state')"; then
			# A closed issue is the point of the list: the pin may be ready to go.
			if [[ "$state" == closed ]]; then printf '**closed**: remove the pin?'; else printf '%s' "$state"; fi
		else
			printf 'unreadable'
		fi
	else
		printf 'not a GitHub issue'
	fi
}

cmd_pins() {
	local tree="$1" type what value newest issue rows=0 pins
	printf '### Pins\n\n'
	while read -r type; do
		[[ -f "$tree/images/cloud/$type/versions.lock" ]] || continue
		# Assigned first, so a failed lookup fails the list.
		pins="$(LOCK_DIR="$tree/images/cloud" "$lock_sh" pins "$type")"
		[[ -n "$pins" ]] || continue
		while IFS=$'\t' read -r what value newest issue; do
			if ((rows == 0)); then
				printf '| Type | Pin | Locked | Upstream now | Issue |\n| --- | --- | --- | --- | --- |\n'
			fi
			rows=$((rows + 1))
			# shellcheck disable=SC2016 # markdown backticks
			printf '| %s | %s | `%s` | `%s` | %s %s |\n' "$type" "$what" "$value" "$newest" \
				"$([[ "$issue" == - ]] || printf '%s' "$issue")" "$(issue_state "$issue")"
		done <<<"$pins"
	done < <(cmd_types all)
	((rows > 0)) || printf 'No pins.\n'
}

# --- The bot's token ----------------------------------------------------------

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# app_jwt: a JSON Web Token for the App, valid ten minutes (GitHub's most),
# signed with its private key. The key goes to openssl through a file
# descriptor, never a file on disk.
app_jwt() {
	local now header payload signature
	[[ "${LOCK_BOT_APP_ID:-}" =~ ^[0-9]+$ ]] || die "LOCK_BOT_APP_ID isn't an App id"
	[[ -n "${LOCK_BOT_PRIVATE_KEY:-}" ]] || die "LOCK_BOT_PRIVATE_KEY is empty"
	now="$(date +%s)"
	header="$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)"
	# Issued a minute early, for clock drift.
	payload="$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' $((now - 60)) $((now + 540)) "$LOCK_BOT_APP_ID" | b64url)"
	signature="$(printf '%s.%s' "$header" "$payload" |
		openssl dgst -sha256 -sign <(printf '%s\n' "$LOCK_BOT_PRIVATE_KEY") -binary | b64url)"
	printf '%s.%s.%s' "$header" "$payload" "$signature"
}

# github <bearer> <method> <path> [body]: one REST call; prints the response,
# fails on an HTTP error. The bearer goes in through a file descriptor.
github() {
	local args=(-fsS -X "$2" -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28')
	[[ -z "${4:-}" ]] || args+=(-H 'Content-Type: application/json' --data-binary "$4")
	curl "${args[@]}" -H @<(printf 'Authorization: Bearer %s\n' "$1") "$api/$3"
}

cmd_token() {
	local jwt name installation response token
	name="$(repo)"
	jwt="$(app_jwt)"
	response="$(github "$jwt" GET "repos/$name/installation")" || die "the App isn't installed on $name"
	installation="$(jq -er '.id' <<<"$response")"
	# Narrowed to this one repository and the two permissions the bot uses: a
	# leaked token can't touch issues, settings or any other repository, and
	# lapses in an hour.
	response="$(github "$jwt" POST "app/installations/$installation/access_tokens" "$(jq -cn --arg repo "${name#*/}" \
		'{repositories: [$repo], permissions: {contents: "write", pull_requests: "write"}}')")" ||
		die "GitHub refused an installation token"
	token="$(jq -er '.token' <<<"$response")"
	printf '%s\n' "$token"
}

cmd_revoke() {
	[[ -n "${GH_TOKEN:-}" ]] || die "GH_TOKEN is empty"
	github "$GH_TOKEN" DELETE installation/token >/dev/null
}

# --- Proposing ----------------------------------------------------------------

ref_sha() { gh api "repos/$(repo)/git/ref/heads/$1" --jq '.object.sha'; }
ref_exists() { gh api "repos/$(repo)/git/ref/heads/$1" --jq '.ref' >/dev/null 2>&1; }

# open_pr: "<number> <node id>" of the open pull request from the bot's
# branch, or nothing.
open_pr() {
	local name
	name="$(repo)"
	gh api "repos/$name/pulls?head=${name%%/*}:$branch&base=main&state=open" --jq '.[0] | select(. != null) | "\(.number) \(.node_id)"'
}

# commit_on <branch> <head> <headline> <body file> <candidate>: one commit of
# the candidate's locks, through createCommitOnBranch (signed by GitHub).
# Prints the new commit's id.
commit_on() {
	local into="$1" head="$2" headline="$3" body="$4" candidate="$5" path additions="[]"
	while read -r path; do
		additions="$(jq -c --arg path "$path" --arg contents "$(base64 -w0 <"$candidate/$path")" \
			'. + [{path: $path, contents: $contents}]' <<<"$additions")"
	done < <(files_in "$candidate")
	jq -n --arg repo "$(repo)" --arg branch "$into" --arg head "$head" --arg headline "$headline" \
		--rawfile body "$body" --argjson additions "$additions" '{
			query: "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }",
			variables: {input: {
				branch: {repositoryNameWithOwner: $repo, branchName: $branch},
				expectedHeadOid: $head,
				message: {headline: $headline, body: $body},
				fileChanges: {additions: $additions}
			}}
		}' | gh api graphql --input - --jq '.data.createCommitOnBranch.commit.oid'
}

cmd_propose() {
	local candidate="$1" title="$2" body="$3" name main commit pr number node
	name="$(repo)"
	files_in "$candidate" | cmd_guard
	# A link would commit what it points at.
	[[ -z "$(find "$candidate" -mindepth 1 -not -type d -not -type f)" ]] || die "the candidate holds a link or device"
	main="$(ref_sha main)"
	# The commit is made on a staging branch at main's head, then lock/watch is
	# moved to it. Moving lock/watch to main itself first would mark the open
	# pull request merged, since its head would be in main.
	if ref_exists "$staging"; then gh api -X DELETE "repos/$name/git/refs/heads/$staging" >/dev/null; fi
	gh api -X POST "repos/$name/git/refs" -f ref="refs/heads/$staging" -f sha="$main" >/dev/null
	commit="$(commit_on "$staging" "$main" "$title" "$body" "$candidate")"
	[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || die "createCommitOnBranch gave no commit"
	if ref_exists "$branch"; then
		gh api -X PATCH "repos/$name/git/refs/heads/$branch" -f sha="$commit" -F force=true >/dev/null
	else
		gh api -X POST "repos/$name/git/refs" -f ref="refs/heads/$branch" -f sha="$commit" >/dev/null
	fi
	gh api -X DELETE "repos/$name/git/refs/heads/$staging" >/dev/null
	pr="$(open_pr)"
	if [[ -n "$pr" ]]; then
		read -r number node <<<"$pr"
		gh api -X PATCH "repos/$name/pulls/$number" -f title="$title" -F body=@"$body" >/dev/null
	else
		read -r number node < <(gh api -X POST "repos/$name/pulls" -f title="$title" -f head="$branch" -f base=main \
			-F body=@"$body" --jq '"\(.number) \(.node_id)"')
	fi
	[[ "$number" =~ ^[0-9]+$ && -n "$node" ]] || die "no pull request from $branch"
	# The ruleset's required check (cloud-build's gate) is what it waits on.
	# shellcheck disable=SC2016 # a GraphQL variable
	gh api graphql -f query='mutation($id: ID!) { enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: SQUASH}) { clientMutationId } }' \
		-f id="$node" >/dev/null
	printf 'pull request #%s at %s, auto-merge on\n' "$number" "$commit"
}

cmd_close() {
	local pr number node
	pr="$(open_pr)"
	[[ -n "$pr" ]] || return 0
	read -r number node <<<"$pr"
	gh api -X PATCH "repos/$(repo)/pulls/$number" -f state=closed >/dev/null
	printf 'closed pull request #%s: the locks on main are current\n' "$number"
}

# Each command runs as a plain call, never after || or &&, where bash switches
# errexit off inside it.
case "${1:-}" in
types)
	(($# == 2)) || usage
	cmd_types "$2"
	;;
changed)
	(($# == 3)) || usage
	cmd_changed "$2" "$3"
	;;
carry)
	(($# == 5)) || usage
	cmd_carry "$2" "$3" "$4" "$5"
	;;
proposed)
	(($# == 4)) || usage
	cmd_proposed "$2" "$3" "$4"
	;;
guard)
	(($# == 1)) || usage
	cmd_guard
	;;
title)
	(($# == 3)) || usage
	cmd_title "$2" "$3"
	;;
body)
	(($# == 3)) || usage
	cmd_body "$2" "$3"
	;;
pins)
	(($# == 2)) || usage
	cmd_pins "$2"
	;;
token)
	(($# == 1)) || usage
	cmd_token
	;;
revoke)
	(($# == 1)) || usage
	cmd_revoke
	;;
propose)
	(($# == 4)) || usage
	cmd_propose "$2" "$3" "$4"
	;;
close)
	(($# == 1)) || usage
	cmd_close
	;;
*) usage ;;
esac
