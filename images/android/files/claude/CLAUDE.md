# This machine

A disposable Linux VM (Ubuntu 24.04, x86_64) that serves this one session and is
deleted when it ends, so push anything worth keeping. You're the image's `ubuntu`
user, with passwordless sudo: `sudo apt-get install` whatever a project needs. On
Google Cloud (the `android` runner type) `$AGENT_SCRATCH` is `/scratch`, on the VM's
own disk: build there.

## Tools

Installed at their latest release when the image was built, so check a repo's
`make doctor` before installing anything:

<!-- toolsets -->

There's no gcloud and no cloud credentials, and none should be added. Work that
needs them (deploys, Terraform plans and applies) runs in the repo's own CI.

## Android

- SDK: `$ANDROID_HOME` (`/opt/android-sdk`), owned by you. JDK 21 is `$JAVA_HOME`.
- Google's `android` CLI is installed, with its `android-cli` skill: use it for
  SDK packages (`android sdk install …`; sdkmanager is deprecated), screenshots,
  UI layout, and docs. Pass `--no-metrics`.
- An emulator (AVD `agent`, x86_64 Google APIs image, KVM-accelerated) boots when
  the VM starts and shows up as `emulator-5554`. Run `agent-emulator wait` before
  `connectedAndroidTest` or anything else that needs a device.
- `agent-emulator status|stop|start` manage it. It starts clean every time and
  saves nothing. For other flags or another AVD, `agent-emulator stop` and run
  `emulator` yourself; keep `-crash-report-mode disabled` (its hang detector
  kills slow boots in a VM), or pass extra flags with
  `AGENT_EMULATOR_ARGS="…" agent-emulator start`.
- The emulator runs without a window: take device screenshots with the
  `android` CLI. The virtual display (`$DISPLAY`) is for desktop apps.
- Gradle Managed Devices work too, since KVM is available. They start their own
  emulators, so stop this one first if memory is tight.
