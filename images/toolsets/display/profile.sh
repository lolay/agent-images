# shellcheck shell=sh
# The display toolset (images/toolsets/display): every shell talks to the Xvfb
# display the entrypoint starts.
export DISPLAY="${DISPLAY:-:99}"
