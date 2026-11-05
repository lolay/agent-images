# This machine

A disposable Linux container on Google Cloud Run (Ubuntu 24.04, x86_64, no GPU, no
display) that serves this one session and is gone when it ends, so push anything
worth keeping. You're the image's `ubuntu` user, without root: Cloud Run sets the
no-new-privileges flag, so `sudo` fails, and with it `apt-get install`. There's no
Android SDK or emulator.

## Tools

Installed at their latest release when the image was built, so check a repo's
`make doctor` before installing anything:

- Node with pnpm (Node no longer bundles corepack), Python (Homebrew's, ahead of
  Ubuntu's on PATH) with uv, Go, Rust (rustup's stable, with clippy and rustfmt), and
  a C toolchain.
- protoc, buf and grpcurl. Language plugins (protoc-gen-go, grpcio-tools,
  protoc-gen-es, tonic-build) come from the project.
- Terraform through tenv, which switches to a repo's `.terraform-version` and
  downloads it if it isn't here, and tflint.
- gh, git, jq, ripgrep, actionlint, shellcheck, mandoc, age, triage and nowline.
- Chromium's system libraries for Playwright: `playwright install chromium` works;
  `playwright install-deps` needs apt and doesn't.

Install anything else with `brew install` (Homebrew is yours), `uv`, `pnpm`,
`go install` or `cargo install`.

## No cloud access

There's no gcloud and no cloud credentials, and none should be added. Terraform
work here is offline: `terraform fmt`, `terraform validate` after
`terraform init -backend=false`, and `tflint`. Plans and applies run in the repo's
own CI.

## Storage

- Work in `$AGENT_SCRATCH` (`/scratch`): a dedicated disk, deleted when this session
  ends. Your repo is checked out there, and `TMPDIR` and the caches (pip, npm, Go,
  Homebrew, `XDG_CACHE_HOME`) point there. Don't write large files elsewhere: the
  rest of the filesystem is in memory, and every file there takes the session's
  memory.
- Keep anything over 10 MB worth keeping in `$AGENT_OUTPUTS` (a Cloud Storage
  bucket, kept 30 days). Finish files on scratch, then copy them there; tell the user
  the `gs://` path from the start-of-session facts.
- Read shared inputs from `$AGENT_DATASETS` (read-only).
- Code still goes to git: commit and push before the session ends.

## Time limit

- The session ends at a set time, and after a stretch with no messages (the
  start-of-session notice gives both). Start your first reply with the notice lines
  from the start-of-session facts.
- Before starting work you expect to take over an hour, say whether it fits before
  the end time, and, when the notice gives the runtime's cost, roughly what it will
  cost. Commit and push well before the end.
