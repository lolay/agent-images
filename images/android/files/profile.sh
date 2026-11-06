# shellcheck shell=sh
# Installed as /etc/profile.d/agent-images.sh. Login shells read it, and systemd
# starts the runner and watchdog through `bash -l`, so the runner, its sessions,
# and any shell share one environment.
export ANDROID_HOME=/opt/android-sdk
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
# Homebrew ahead of the system, as `brew shellenv` puts it; rustup's bin holds
# the cargo and rustc proxies, ~/.cargo/bin gets what `cargo install` installs
# (cargo-ndk), and ~/go/bin what `go install` does (gomobile). python3 is
# Ubuntu's: the image has no python toolset.
PATH="$HOME/bin:$HOME/.local/bin:/home/linuxbrew/.linuxbrew/opt/rustup/bin:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH:$HOME/.cargo/bin:$HOME/go/bin"
export PATH
# What the toolsets recorded (images/toolsets/install-toolset.sh): display's
# DISPLAY, android's ANDROID_NDK_HOME.
for agent_file in /etc/agent-images/toolsets/*.profile.sh; do
	# shellcheck source=/dev/null
	[ -f "$agent_file" ] && . "$agent_file"
done
unset agent_file
