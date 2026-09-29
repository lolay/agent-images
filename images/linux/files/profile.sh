# shellcheck shell=sh
# Installed as /etc/profile.d/agent-images.sh. Login shells read it, and systemd
# starts the runner and watchdog through `bash -l`, so the runner, its sessions,
# and any shell share one environment.
export ANDROID_HOME=/opt/android-sdk
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
PATH="$HOME/bin:$HOME/.local/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
export PATH
