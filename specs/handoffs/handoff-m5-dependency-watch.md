# Handoff: m5, dependency watch

Written 2026-11-06, at the end of m4. The design is [specs/gcp.md](../gcp.md)
"m5 - Dependency watch"; m4's design and results are just above it.

Review: r2 (`specs/reviews/2026-11-06-r2-incremental.md`, lolay/agent-images#50)
found nothing high. Its medium and decisions are folded into the plan,
[plan-m5-sextant.md](plan-m5-sextant.md).

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

m4's first deploy (run 42283134638) showed `-next` passing in Cloud Run for
`linux` and `gis`, and every android binary check and smoke test on the smoke VM,
android-go and android-rust included (gcp.md, "m4 results"). Left:

- The android versions report's summary and artifact on the `vm-images` job
  (lolay/agent-images#52: Packer downloads it from the build VM), at the next
  android deploy.
- gpu's live smoke test, once `linux-gpu`'s quota lands and `DEPLOY=false` goes
  (lolay/agent-images#47).

If one fails, the `/agent-toolset-checks` skill in a session on that image
reruns it and offers to file an issue.

## Where m5 stands (2026-11-06)

PR A (lolay/agent-images#54, `10f4e44`) builds every Cloud Run type from its lock.
Group 4 (s10 to s12) is on `claude/m5-lock-watch`: `lock-watch.yml`,
`scripts/cloud/lock-watch.sh`, cloud-build's `gate`, and `CLAUDE_UPGRADE=false` on
`nightly` types. Before PR B can merge usefully, the owner's setup in the plan
(after s12): the App, the `lock-bot` environment and its two secrets, auto-merge
allowed, `gate` required with zero approvals, and the cleanup policy out of dry
run. s13 verifies on `main` after PR B merges.

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
- **`lock-watch.yml` runs only on `main`** (`resolve`'s `if`, and `lock-bot`'s
  branch policy), so nothing on a branch exercises `propose` against GitHub. The
  dry run of `resolve` on PR B's branch (a throwaway job, deleted) is the closest.
- **`make cloud-lint` needs Go now,** for `actionlint` (`go run`, Go's module proxy
  and checksum database). actionlint runs shellcheck on every `run:` block, so a
  markdown printf in a workflow needs `# shellcheck disable=SC2016`.
- **`cloud-build` runs on every pull request** and `gate` is the required check.
  A new path the check covers goes in the `changes` job's pattern, not a trigger
  filter.
