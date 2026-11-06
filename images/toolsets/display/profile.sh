# shellcheck shell=sh
# The display toolset (images/toolsets/display): every shell talks to the Xvfb
# display that the cloud entrypoint, or the Android image's agent-xvfb.service,
# starts.
export DISPLAY="${DISPLAY:-:99}"
