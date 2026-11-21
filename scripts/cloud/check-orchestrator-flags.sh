#!/bin/bash
# Checks the flags the supervisor passes to `claude self-hosted-runner
# orchestrator` (images/cloud/orchestrator/orchestrator-args.sh) against that
# command's --help in an orchestrator image: a Claude release that renamed or
# dropped one would make every orchestrator exit at start, restarted forever
# (r1.f10). The deploy runs it on the pushed image before pointing the host at
# it (cloud-deploy.yml); the pull request check on the image it built
# (cloud-build.yml), since the unit tests' fake claude reads the same list and
# can't catch a typo in it.
#
#   scripts/cloud/check-orchestrator-flags.sh <image>
#
# A flag counts as listed only where the help defines it: on an option line
# (one that starts with "-"), before the description, so a mention in another
# option's description ("alias for --x") doesn't. An image docker can't run
# fails with docker's own error, not as missing flags.
set -euo pipefail

# shellcheck source=images/cloud/orchestrator/orchestrator-args.sh
source "$(dirname "${BASH_SOURCE[0]}")/../../images/cloud/orchestrator/orchestrator-args.sh"

log() { printf 'check-orchestrator-flags: %s\n' "$*"; }

if (($# != 1)); then
	log "usage: check-orchestrator-flags.sh <image>" >&2
	exit 2
fi
readonly image="$1"

# The help is read whole first, so a failed run reports its own error.
if ! help="$(docker run --rm --entrypoint claude "$image" self-hosted-runner orchestrator --help 2>&1)"; then
	log "couldn't run $image's claude self-hosted-runner orchestrator --help:" >&2
	printf '%s\n' "$help" >&2
	exit 1
fi
if [[ -z "${help//[[:space:]]/}" ]]; then
	log "$image's claude self-hosted-runner orchestrator --help printed nothing" >&2
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
log "all $(orchestrator_flags | wc -l) flags the supervisor passes are listed"
