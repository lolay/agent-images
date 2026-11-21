#!/bin/bash
# Checks the flags the supervisor passes to `claude self-hosted-runner
# orchestrator` (images/cloud/orchestrator/orchestrator-args.sh) against that
# command's --help, read on stdin. The deploy runs it on the pushed
# orchestrator image's own Claude before pointing the host at the image
# (.github/workflows/cloud-deploy.yml): a release that renamed or dropped a flag
# would make every orchestrator exit at start, restarted forever (r1.f10).
#
#   docker run --rm --entrypoint claude <image> self-hosted-runner orchestrator --help 2>&1 |
#     scripts/cloud/check-orchestrator-flags.sh
#
# A flag counts as listed only where the help defines it: on an option line
# (one that starts with "-"), before the description, so a mention in another
# option's description ("alias for --x") doesn't.
set -euo pipefail

# shellcheck source=images/cloud/orchestrator/orchestrator-args.sh
source "$(dirname "${BASH_SOURCE[0]}")/../../images/cloud/orchestrator/orchestrator-args.sh"

help="$(cat)"
if [[ -z "${help//[[:space:]]/}" ]]; then
	printf 'check-orchestrator-flags: no --help text on stdin\n' >&2
	exit 1
fi
# Each option line's spec, the part before the gap that starts its description.
defined="$(sed -n -E '/^[[:space:]]*-/{s/^[[:space:]]+//; s/ {2,}.*//; p;}' <<<"$help" |
	grep -oE -- '--[A-Za-z0-9][-A-Za-z0-9]*' | sort -u)" || defined=""

missing=()
while read -r flag; do
	grep -qxF -- "$flag" <<<"$defined" || missing+=("$flag")
done < <(orchestrator_flags)

if ((${#missing[@]} > 0)); then
	printf 'check-orchestrator-flags: this Claude'"'"'s orchestrator --help lists no %s, which the supervisor passes (images/cloud/orchestrator/orchestrator-args.sh)\n' "${missing[@]}" >&2
	exit 1
fi
printf 'check-orchestrator-flags: all %s flags the supervisor passes are listed\n' "$(orchestrator_flags | wc -l)"
