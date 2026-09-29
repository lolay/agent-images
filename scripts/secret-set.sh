#!/bin/bash
# Stores a runner secret in the host's login Keychain as service
# agent-images.<name>, account <account> (default "default").
#
# Usage: scripts/secret-set.sh <name> [account]
#   Prompts for the value (hidden), or reads it from stdin when piped:
#   pbpaste | scripts/secret-set.sh claude-environment-secret
#
# Not `security add-generic-password -w` with no value: its prompt silently
# truncates at 128 characters, and environment keys are longer. Not -w <value>
# either, which would show the secret in `ps`. The command goes to `security -i`
# over stdin instead, and the stored value is read back and compared.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/lib.sh
source "$script_dir/lib.sh"

readonly name="${1:?usage: secret-set.sh <name> [account]}"
readonly account="${2:-default}"
readonly service="$KEYCHAIN_SERVICE_PREFIX.$name"

if [[ -t 0 ]]; then
	# A terminal line holds at most 1024 characters; a pipe has no limit.
	printf 'Piping avoids terminal limits: pbpaste | make secret-set NAME=%s\n' "$name" >&2
	read -r -s -p "Value for $service (account $account): " value
	printf '\n' >&2
else
	value="$(cat)"
fi
# A copied value often carries a trailing newline or spaces.
value="${value#"${value%%[![:space:]]*}"}"
value="${value%"${value##*[![:space:]]}"}"

[[ -n "$value" ]] || die "empty value; nothing stored"
# The value goes inside double quotes in a security -i command line.
[[ "$value" != *[\"\\]* && "$value" != *$'\n'* ]] ||
	die "the value contains a quote, backslash, or newline, which this script can't pass to security safely"

printf 'add-generic-password -U -s "%s" -a "%s" -w "%s"\n' "$service" "$account" "$value" |
	security -i >/dev/null

stored="$(security find-generic-password -s "$service" -a "$account" -w 2>/dev/null)" ||
	die "security didn't store $service"
[[ "$stored" == "$value" ]] || die "the stored value doesn't match what was entered"
log "stored $service for $account (${#value} characters)"
