# shellcheck shell=sh
# Installed as /etc/profile.d/agent-images.sh. Login shells read it, and systemd
# starts the runner and watchdog through `bash -l`, so the runner, its sessions,
# and any shell share one environment.
export ANDROID_HOME=/opt/android-sdk
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
# A repo's .terraform-version that isn't the image's Terraform downloads on use.
export TENV_AUTO_INSTALL=true
# Homebrew ahead of the system, as `brew shellenv` puts it, so python3 is
# Homebrew's current release rather than Ubuntu's; rustup's bin holds the cargo
# and rustc proxies, and ~/.cargo/bin gets what `cargo install` installs.
PATH="$HOME/bin:$HOME/.local/bin:/home/linuxbrew/.linuxbrew/opt/rustup/bin:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH:$HOME/.cargo/bin"
export PATH
