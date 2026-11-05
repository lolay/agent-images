# This machine

A disposable Linux VM (Ubuntu 24.04, x86_64) that serves this one session and is
deleted when it ends, so push anything worth keeping. You're the image's `ubuntu`
user, with passwordless sudo: `sudo apt-get install` whatever a project needs.

## Tools

Installed at their latest release when the image was built, so check a repo's
`make doctor` before installing anything:

- Node with pnpm (Node no longer bundles corepack), Python (Homebrew's, ahead of
  Ubuntu's on PATH) with uv, Go, and Rust (rustup's stable, with clippy and rustfmt).
- A C toolchain, cmake, ninja and pkg-config, with the headers native builds most
  often need (OpenSSL, zlib, libffi, SQLite, libpq, bzip2, xz, readline), and the
  sqlite3 and psql clients. You have sudo, so `sudo apt-get install` adds others.
- protoc, buf and grpcurl. Language plugins (protoc-gen-go, grpcio-tools,
  protoc-gen-es, tonic-build) come from the project.
- Terraform through tenv, which switches to a repo's `.terraform-version` and
  downloads it if it isn't here, and tflint.
- gh, git, jq, ripgrep, tmux, actionlint, shellcheck, mandoc, age, sops, triage and
  nowline.
- Chromium's system libraries for Playwright.

There's no gcloud and no cloud credentials, and none should be added. Terraform
work here is offline: `terraform fmt`, `terraform validate` after
`terraform init -backend=false`, and `tflint`. Plans and applies run in the repo's
own CI.

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
  `emulator` yourself; keep `-no-window` (no display) and
  `-crash-report-mode disabled` (its hang detector kills slow boots in a VM), or
  pass extra flags with `AGENT_EMULATOR_ARGS="…" agent-emulator start`.
- Gradle Managed Devices work too, since KVM is available. They start their own
  emulators, so stop this one first if memory is tight.
