#!/bin/bash
# Prints an env file's settings as $GITHUB_ENV lines, so a workflow job reads
# them as every script does: by sourcing the file in a subshell, not by grepping
# it, which kept a quoted value's quotes (r1.f33). A value with a newline gets
# GitHub's delimiter form.
#
#   scripts/cloud/github-env.sh >>"$GITHUB_ENV"                       infra/gcp/project.env,
#                                                                    through gcp_load_env
#   scripts/cloud/github-env.sh scripts/ci/tools.env >>"$GITHUB_ENV" that file, sourced as is
#
# ci.yml runs it on macOS, whose /bin/bash is 3.2, so the file path keeps to
# bash 3.2 (no mapfile, no associative arrays); only project.env loads
# hooks/gcp/lib.sh, which the macOS job never asks for.
set -euo pipefail

if (($# > 1)); then
	printf 'usage: github-env.sh [<env file>]\n' >&2
	exit 2
fi
if (($# == 1)); then
	env_file="$1"
else
	# shellcheck source=hooks/gcp/lib.sh
	source "$(dirname "${BASH_SOURCE[0]}")/../../hooks/gcp/lib.sh"
	env_file="$AGENT_GCP_ENV_DIR/project.env"
fi
readonly env_file
if [[ ! -f "$env_file" ]]; then
	printf 'github-env: no %s\n' "$env_file" >&2
	exit 1
fi

# The names the file assigns; their values come from the shell.
names=""
while IFS= read -r name; do
	names="$names $name"
done < <(sed -n -E 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "$env_file")
(
	if (($# == 1)); then
		# shellcheck source=/dev/null
		source "$env_file" || exit 1
	else
		gcp_load_env || exit 1
	fi
	for name in $names; do
		value="${!name}"
		if [[ "$value" == *$'\n'* ]]; then
			delimiter="EOF_$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
			printf '%s<<%s\n%s\n%s\n' "$name" "$delimiter" "$value" "$delimiter"
		else
			printf '%s=%s\n' "$name" "$value"
		fi
	done
)
