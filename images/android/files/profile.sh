# shellcheck shell=sh
# Installed as /etc/profile.d/agent-images.sh. Login shells read it, and systemd
# starts the runner and watchdog through `bash -l`, so the runner, its sessions,
# and any shell share one environment.
#
# All of it is the toolsets' (images/toolsets/toolset-profile.sh): PATH, with
# Homebrew ahead of the system, and what the toolsets recorded, the android
# toolset's SDK (ANDROID_HOME and its tools on PATH), the jvm toolset's JAVA_HOME,
# display's DISPLAY, and the rust and go toolsets' install bins (cargo-ndk,
# gomobile). python3 is Ubuntu's: the image has no python toolset.
agent_toolset_profile="${AGENT_TOOLSET_PROFILE:-/etc/agent-images/toolset-profile.sh}"
# shellcheck source=images/toolsets/toolset-profile.sh
[ -f "$agent_toolset_profile" ] && . "$agent_toolset_profile"
unset agent_toolset_profile
