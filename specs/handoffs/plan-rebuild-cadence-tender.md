# Per-type rebuild cadence for the GCP images

Status: s1, s2 done; s3 next.

## Context

Today nothing rebuilds on a schedule: an image is only as fresh as the last push that
touched its inputs (`cloud-deploy.yml`, push to `main` + manual dispatch). m4's design
(`specs/gcp.md`, "Image pipeline") assumes one cadence for every type: an hourly watch,
a nightly no-cache rebuild, and `CLAUDE_UPGRADE=false` everywhere. That's fine for
`linux` (Docker layer cache, a Claude bump rebuilds one layer) and wrong for `android`:
Packer has no layer cache, so every build is the full ~45 min on an `n2-standard-8`
plus a >5 min disk capture (#28) and a smoke VM.

Staleness is cheap to tolerate anyway: every runner already self-updates Claude at
start (`images/shared/runners/claude.sh`, `CLAUDE_UPGRADE=true`, ~6 s measured), and
the slow-moving stuff (SDK, conda-forge stack, apt) doesn't need daily freshness.

## Recommendation

| Image | Rebuild cost | Cadence | Claude self-update |
| --- | --- | --- | --- |
| `linux` (Cloud Run) | Minutes; cached, Claude is the last layer | Nightly no-cache, Tue to Sat; m4's hourly watch later | Off once m4's watch lands |
| `orchestrator` | ~1 to 2 min, tiny | Nightly with `linux` | n/a |
| `gis` | 9 GB; conda-forge; blows most of the 10 GB GHA cache | Weekly no-cache (Sunday) | Stays on |
| `linux-gpu`, `3d-gpu` (m3) | CUDA, cache misses expected | Weekly, same class as `gis` | Stays on |
| `android` (GCE) | ~45 min + capture + smoke VM, no cache; ~$0.50 in GCP per build | Weekly (Sunday, after `gis`) | Stays on, permanently |
| macOS Tart, Linux-host LXD | Manual, `make build` | Manual, on an Xcode/OS release or roughly monthly (unchanged) | Stays on |

All types still rebuild on push when their own inputs change (the existing deploy map).
I'm not narrowing `android`'s push trigger for `images/shared/**`: those changes are
rare outside active development, and shipping a runner fix to one type but not another
is worse than 45 minutes of CI.

## Changes

Branch: `claude/happy-heisenberg-g5hp5v` (harness-assigned). This plan lives at
`specs/handoffs/plan-rebuild-cadence-tender.md` until merge; remove it as the
last commit before merge.

### s1 - Cadence per type in the env files [exec] (done)

- Add `REBUILD=nightly|weekly` to `infra/gcp/linux.env` (nightly),
  `infra/gcp/gis.env` (weekly), `infra/gcp/android.env` (weekly), with a comment
  pointing at `specs/gcp.md` "Rebuild cadence". The orchestrator isn't a type; it rides
  `nightly`, stated in the spec and the script (`project.env` holds Terraform outputs
  only, per its header).
- Side effect, intended: touching the env files redeploys each job (no image build) and
  the orchestrator on the next push. Harmless.

### s2 - `changed-targets.sh --schedule <class>` [exec] (done)

- `scripts/cloud/changed-targets.sh`: new mode `--schedule nightly|weekly` that
  emits the same JSON shape as `--all`, filtered to types whose `REBUILD` matches
  (read through the existing `hooks/gcp/lib.sh` env loading, as `gcp_types` does),
  `build: true` for each, `orchestrator: true` only for `nightly`, `prune: false`.
  A type with no `REBUILD` defaults to `nightly`, so a new type is never silently left
  unscheduled. Unknown class: exit 2 with usage.
- `tests/unit/test_changed_targets.py`: cases for each class, the default, and a bad
  class, using the existing fixtures in `tests/unit/support.py`.

### s3 - Schedules in `cloud-deploy.yml` [exec]

- `on.schedule`, `timezone: America/Los_Angeles`:
  - nightly `7 4 * * 2-6` (spec's 04:00 Tue to Sat, off the hour)
  - weekly `37 5 * * 0` (Sunday; the deploy's types job runs `max-parallel: 1`, and
    `vm-images` runs alongside, so `gis` and `android` don't contend)
- `plan` job: on `github.event_name == 'schedule'`, map `github.event.schedule` (the
  cron string) to the class and call `changed-targets.sh --schedule <class>`.
- `no-cache`: `inputs.no-cache == true || github.event_name == 'schedule'` in both bake
  steps (types, orchestrator), so a scheduled build picks up apt and Homebrew moves,
  which is the point of it. Packer has no cache, nothing to change there.
- The push-diff base query already filters `--event push`, so scheduled runs don't
  shift it. Concurrency group already queues.

### s4 - Spec [exec]

- `specs/gcp.md`: new "Rebuild cadence" subsection under "Image pipeline" with the
  table above and the reason (build cost vs. how fast contents move, Claude
  self-updates regardless); rewrite "Nightly" to point at it; m4: the hourly watch
  covers `nightly` types only and `CLAUDE_UPGRADE=false` applies only to them;
  deploy-map table notes `REBUILD`.
- `specs/gcp.md`, the `android` image bullet: say why it builds in GCP, not on the
  GitHub runner (asked for mid-session): the output is a Compute Engine image, the
  emulator snapshot needs KVM in the build VM, and building a 100 GB raw disk on a
  hosted runner to upload and import is slower and more fragile; the GitHub job only
  drives Packer and waits.
- `specs/claude-releases.md` "Proposal": one line that the hourly watch and nightly
  apply to the fast types, slow ones keep self-updating.

Deferred, noted in the spec only: splitting `android` into a base image (toolsets, SDK,
AVD snapshot) and a thin top layer (`images/shared`, Claude). Worth it only if shared
changes keep triggering android builds; the disk capture still costs every build.

## Verification

- `make cloud-lint cloud-test` (shellcheck, shfmt, packer validate, unit tests) green.
- `scripts/cloud/changed-targets.sh --schedule nightly|weekly` locally: expect
  `linux`+orchestrator / `gis`+`android`.
- After merge: a manual `workflow_dispatch` is unchanged (still works for ad-hoc); the
  first scheduled nightly shows up in Actions as `schedule` with only `linux` and
  `orchestrator` in its matrix. Weekly verified when it first fires (or note in the PR
  that it's unverified until then).
