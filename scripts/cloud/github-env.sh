#!/bin/bash
# Prints infra/gcp/project.env's settings as $GITHUB_ENV lines, so a workflow job
# reads them as every script does: by sourcing the file in a subshell
# (hooks/gcp/lib.sh's gcp_load_env), not by grepping it, which kept a quoted
# value's quotes (r1.f33). A value with a newline gets GitHub's delimiter form.
#
#   scripts/cloud/github-env.sh >>"$GITHUB_ENV"
set -euo pipefail

# shellcheck source=hooks/gcp/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../../hooks/gcp/lib.sh"
readonly project_env="$AGENT_GCP_ENV_DIR/project.env"
if [[ ! -f "$project_env" ]]; then
	printf 'github-env: no %s\n' "$project_env" >&2
	exit 1
fi

# The names the file assigns; their values come from the shell.
mapfile -t names < <(sed -n -E 's/^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=.*/\1/p' "$project_env")
(
	gcp_load_env || exit 1
	for name in "${names[@]}"; do
		value="${!name}"
		if [[ "$value" == *$'\n'* ]]; then
			delimiter="EOF_$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
			printf '%s<<%s\n%s\n%s\n' "$name" "$delimiter" "$value" "$delimiter"
		else
			printf '%s=%s\n' "$name" "$value"
		fi
	done
)
