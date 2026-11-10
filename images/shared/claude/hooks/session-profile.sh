#!/bin/bash
# Claude Code SessionStart hook for every image (the runner seeds ~/.claude into
# each session). Claude Code's shell snapshot ends by exporting the PATH it was
# taken with, which lacks the session's gh shim; every Bash tool command sources
# $CLAUDE_ENV_FILE after the snapshot, so this has it source the image's profile
# there, which puts the shim back in front (images/shared/profile-gh-shim.sh).
# An image without that profile (macOS) gets nothing.
#
# stdin: the hook's JSON payload, unused. stdout: nothing. It never fails the
# session.
set -uo pipefail

: "${AGENT_PROFILE:=/etc/profile.d/agent-images.sh}"
cat >/dev/null

[[ -n "${CLAUDE_ENV_FILE:-}" && -f "$AGENT_PROFILE" ]] || exit 0
# Once: a resumed session keeps its env file. Match the line as written, since
# %q escapes what the path's own text wouldn't match.
line="$(printf '. %q' "$AGENT_PROFILE")"
grep -qxF -- "$line" "$CLAUDE_ENV_FILE" 2>/dev/null ||
	printf '%s\n' "$line" >>"$CLAUDE_ENV_FILE"
exit 0
