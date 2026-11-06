#!/bin/bash
# The display toolset's smoke test (specs/gcp.md, m4): the virtual display
# answers, OpenGL renders on it (Mesa's llvmpipe here), and a screenshot of it is
# a PNG. The entrypoint starts Xvfb before the smoke, as a session gets it.
set -euo pipefail

: "${DISPLAY:?no DISPLAY, which the display toolset profile sets}"
dimensions="$(xdpyinfo -display "$DISPLAY" | sed -n 's/^ *dimensions: *\([0-9x]*\).*/\1/p')"
renderer="$(glxinfo -B | sed -n 's/^OpenGL renderer string: //p')"
[[ -n "$renderer" ]] || {
	echo "display: no OpenGL renderer on $DISPLAY"
	exit 1
}
import -window root screenshot.png
size="$(identify -format '%wx%h %m' screenshot.png)"
echo "display: $DISPLAY at $dimensions, OpenGL on $renderer, screenshot $size"
