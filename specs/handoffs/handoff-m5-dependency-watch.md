# Handoff: m5, dependency watch

Written 2026-11-06, at the end of m4. The design is [specs/gcp.md](../gcp.md)
"m5 - Dependency watch"; m4's design and results are just above it.

Review: pending, after m4's PR (lolay/agent-images#48) merges. Run an incremental
review of `main` from r1's anchor first (`specs/reviews/`), and fold its high
findings into m5's plan.

## What m4 gives m5

- **The gate.** The pull request check loads every runner type and runs its binary
  checks and smoke tests (`make cloud-smoke`, `smoke pr`). m5's lock pull requests
  merge on that check. A smoke test marked `# smoke: live` (gpu's) is skipped
  there and runs at `-next`.
- **The record.** Every image has `/etc/agent-images/versions.md`: Homebrew with
  dependencies, apt, rustup, tenv, pixi and the binary checks' versions. The deploy
  (android's report included, downloaded from the Packer build VM) and the pull
  request check put it in the run summary and keep it as a 90-day artifact. m5's
  lock pins what the report only records.
- **One runner for the checks,** `agent-toolset-checks`
  (`images/toolsets/toolset-checks.sh`), shared by the cloud entrypoint, the
  android smoke VM and the `/agent-toolset-checks` session skill.

## Still open from m4 (shown only from `main`)

- The first deploy's `-next` smoke lines in Cloud Run (linux, gis). The smoke
  execution now has `--task-timeout=1800s` (r1.f8).
- The android binary checks and smoke tests on the smoke VM, and the android
  versions report's summary and artifact on the `vm-images` job (Packer downloads
  it from the build VM). android-go and android-rust have never run anywhere.
  Expect them at the next weekly android deploy (Sunday 05:37
  Pacific), or start one with a manual `cloud-deploy` dispatch with targets
  `android` (about 45 min, $0.50).
- gpu's live smoke test, once `linux-gpu`'s quota lands and `DEPLOY=false` goes
  (lolay/agent-images#47).

If any of these fails, the `/agent-toolset-checks` skill in a session on that image
reruns it and offers to file an issue.

## Gotchas

- **Cloud sessions have no Docker daemon.** `docker buildx bake --print` still
  works, so `tests/unit/test_bake.py` runs here. Image-level checks run only in
  `cloud-build.yml` on a pull request.
- **A `smoke.sh` change rebuilds every type that installs that toolset**
  (`changed-targets.sh`). A change to `toolset-checks.sh`, `write-versions.sh` or
  `images/toolsets/skills/` rebuilds every type and the android image.
- **The versions report layer reruns on every commit,** since its `AGENT_COMMIT`
  build arg changes. It takes seconds and sits last, after Claude.
- **The fake gcloud in `tests/unit/test_deploy.py` refuses flags it doesn't know.**
  A new `gcloud` flag in `deploy.sh` needs adding to its `allow` list (AGENTS.md
  "Test fakes").
- **r1.f27 is only partly closed.** The PATH and profile assembly, and
  `android-image-smoke.yml`'s hand-listed checks, are still separate copies.
