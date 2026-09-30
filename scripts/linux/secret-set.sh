#!/bin/bash
# Stores a runner secret on this Linux host as ~/.config/agent-images/<name>, or
# <name>.<account> for one VM (account "default" is the plain file), owner-only.
# host_secret reads it. The Linux counterpart of scripts/secret-set.sh.
#
# Usage: scripts/linux/secret-set.sh <name> [account]
#   Prompts for the value (hidden), or reads it from stdin when piped:
#   cat key.txt | scripts/linux/secret-set.sh claude-environment-secret
#
# The value is trimmed, written to a temp file and renamed into place, then read
# back and compared, so a cut-off paste fails here rather than at the
# orchestrator.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/linux/lib.sh
source "$script_dir/lib.sh"

readonly name="${1:?usage: secret-set.sh <name> [account]}"
readonly account="${2:-default}"
if [[ "$account" == "default" ]]; then
	readonly file="$SECRETS_DIR/$name"
else
	readonly file="$SECRETS_DIR/$name.$account"
fi

if [[ -t 0 ]]; then
	# A terminal line holds at most 4095 characters; a pipe has no limit.
	printf 'Piping avoids terminal limits: cat key.txt | make secret-set NAME=%s\n' "$name" >&2
	read -r -s -p "Value for $name (account $account): " value
	printf '\n' >&2
else
	value="$(cat)"
fi
# A copied value often carries a trailing newline or spaces.
value="${value#"${value%%[![:space:]]*}"}"
value="${value%"${value##*[![:space:]]}"}"

[[ -n "$value" ]] || die "empty value; nothing stored"
[[ "$value" != *$'\n'* ]] || die "the value spans more than one line"

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"
(
	umask 077
	printf '%s' "$value" >"$file.partial"
)
mv -f "$file.partial" "$file"

[[ "$(<"$file")" == "$value" ]] || die "the stored value doesn't match what was entered"
log "stored $file for $account (${#value} characters)"
