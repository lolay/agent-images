# Plan: m5, the dependency watch

Status: approved as written (owner, 2026-11-06), D1 to D9 as recommended, and the
draft PR for s4. Groups 1 to 3 (s1 to s9) done; PR A, lolay/agent-images#54,
merged as `10f4e44`. Group 4 approved (owner, 2026-11-06): s10 to s12 done on
branch `claude/m5-lock-watch` (from `main` at `10f4e44`), opened as PR B,
lolay/agent-images#56 (owner's go, 2026-11-07). STOP: PR B waits for your setup
(the list after s12; require `gate` only after the merge) and your review and
merge. s13 runs on `main` after that.

The design is m5 in [specs/gcp.md](../gcp.md), with "The lock", "Watch", "The lock
bot", "Rebuild cadence" and "Pinning a dependency" above it. Context:
[handoff-m5-dependency-watch.md](handoff-m5-dependency-watch.md), m4's results, and
r2 ([2026-11-06-r2-incremental.md](../reviews/2026-11-06-r2-incremental.md)).

Done when a Claude release reaches `linux` with no human involved: the watch opens a
lock pull request, `cloud-build` loads every runner type and smokes it with
`make cloud-smoke`, the pull request merges itself, and the deploy promotes it.

## Where things stand

- **r2 has no high findings.** Its medium, r2.f1 (an empty toolset record passes
  every gate), is in the gate m5's pull requests merge on, so it's a step here
  (s3), with r2.f2 (no test drives `smoke pr` to a pass) beside it.
- **Two of m4's leftovers are proven** by m4's own deploy (run 42283134638, green).
  The android smoke VM ran every binary check and smoke test, including
  android-go and android-rust for the first time, and booted the emulator in
  27.3 s cold and 6.2 s warm. `linux` and `gis` passed `-next` in Cloud Run and
  were promoted, each with its report in the summary. Still open: gpu's live smoke
  test, which waits on `linux-gpu`'s quota. s1 records this.
- **apt snapshots work from here.** In this session (Ubuntu 24.04, apt 2.8.3),
  `apt-get --snapshot 20261101T000000Z update` fetched from snapshot.ubuntu.com.
  A third-party source (Docker's, here) was fetched live, with no error and no
  snapshot. So on `linux-gpu`, anything apt takes from NVIDIA's CUDA repo floats
  like Homebrew does. See D3.

## Standing decisions (owner)

- Homebrew stays for everything it installs, toolchains included. In the lock its
  versions are a record and a build trigger, never a pin.
- Renovate stays out of the lock. I read this as: it keeps the one job it already
  has (the SHA-pinned GitHub Actions, `.github/renovate.json`), and nothing in
  `versions.lock` or the images goes through it. If you meant to remove it
  entirely, say so and that becomes its own step.
- Lock pull requests merge on m4's check: every runner type loaded and smoked with
  `make cloud-smoke`.
- One open, auto-merging pull request at a time, from the bot's own branch, with
  `main`'s ruleset applying to the bot too.

## Decisions for you

Each has a recommendation; the plan is written as if you take it.

- **D1 The lock's format: one fact per line.** Each line is `<kind> <name> <value>`,
  sorted, under a header naming the writer. For example `base ubuntu
  mirror.gcr.io/library/ubuntu:24.04@sha256:…`, `apt-snapshot - 20261106T150000Z`,
  `claude - 2.1.318`, `rust - 1.99.1`, `terraform - 1.14.2`, `release tflint
  v0.60.0`, `release virtualgl 3.1.3`, `brew go 1.27.3`, `apt cmake
  3.28.3-1build7`, and `pinned <kind> <name> <url>` for each pin. A diff reads as
  what moved, the watch builds its title from the diff, and bash reads it with no
  parser. The other choice is JSON, which is easier to extend but makes worse diffs.
- **D2 android: no lock in m5, but its report gets to the deploy summary** (r2.f11,
  option a). The lock in the spec is built for containers: android's base is a GCE
  image family, and its SDK comes from `sdkmanager`, which isn't in the lock's
  table. android is `weekly`, and its sessions update Claude themselves. So its
  versions report is its only record, and it should land where the other types'
  reports do: download it from the Packer build, add the summary and artifact
  steps to `vm-images`, and fix the passage in the m5 handoff that says the lock
  reads these reports (it doesn't: the lock never reads an image). An android lock
  can be its own milestone if a weekly rebuild ever breaks it.
- **D3 What the lock can't pin is recorded or left visible.** That's Homebrew (by
  your rule), the NVIDIA apt repo's packages on `linux-gpu`, `gis`'s conda-forge
  solve (pixi has no lock in the repo), and the Homebrew installer from `HEAD`.
  The NVIDIA packages are recorded as `apt` lines with a `floating` marker. pixi
  and the installer are left out and named in gcp.md. The scheduled rebuild still
  catches all of them. Committing `pixi.lock` would pin `gis`'s Python stack, but
  that's a separate decision.
- **D4 A weekly watch run covers every type.** The hourly run resolves the
  `nightly` types (just `linux` today). A weekly run, Sunday 04:47 Pacific (50
  minutes before the weekly rebuild), resolves them all. Both share one branch
  (`lock/watch`) and one pull request, so one run never drops another's
  candidate.
- **D5 The apt snapshot holds only during the build.** The base layer writes
  `APT::Snapshot` into `/etc/apt/apt.conf.d/`, so every toolset's
  `apt-get update` resolves at the locked timestamp. The runner stage removes it
  at the end, so a session's own `sudo apt-get` sees today's archive rather than a
  week-old snapshot.
- **D6 `CLAUDE_UPGRADE=false` follows `REBUILD`, with no separate setting.**
  `gcp_runner_settings` emits it for `nightly` types, reading the same
  rebuild-class helper `changed-targets.sh` uses. A type moved to `weekly` gets
  self-update back on its own, and the class's default (`nightly`) lives in one
  place.
- **D7 r2.f16: take its recommendation.** `write-versions.sh` becomes the one
  writer of `/etc/agent-images/versions.md`, and the record directory is one
  variable (`AGENT_TOOLSETS_DIR`, set once in the profiles), which r2.f1's test
  override needs anyway.
- **D8 The orchestrator stays unlocked.** It isn't a runner type: it keeps
  `latest` Claude, rebuilt nightly, as it does now.
- **D9 Two pull requests.** PR A covers s1 to s9: the build consumes the lock,
  the locks are seeded, the gate is fixed and android gets its report. That
  proves building from the lock on a real deploy before any bot runs. PR B covers
  s10 to s13: the watch, the bot, Claude's self-update off, and the cleanup
  policy. Between them, the locks change only through `make cloud-lock`, and
  sessions keep self-updating Claude. One PR would be about 2,500 changed lines.

## Shape

```
before (main at ba55d96)                       after
-------------------------                      -----
FROM ubuntu:24.04 (tag)                        FROM ubuntu@sha256 from versions.lock
apt: today's archive                           apt: APT::Snapshot from the lock (build only)
CLAUDE_VERSION: curl latest in each workflow   CLAUDE_VERSION from the lock
rustup stable, tenv latest,                    the locked versions, resolved within
tflint/VirtualGL latest release                each install.sh's declared constraint
Homebrew: current                              Homebrew: current (lock records it)
nothing proposes updates                       lock-watch.yml: hourly (nightly types),
                                               weekly (all) -> lock/watch -> PR,
                                               auto-merge on cloud-build's gate
sessions self-update Claude everywhere         nightly types: CLAUDE_UPGRADE=false
cleanup policy dry run                         cleanup policy live (lolay/infrastructure)
```

One reader of the lock, `scripts/cloud/lock.sh`, does three jobs: it resolves a
candidate (`resolve <type>`), turns a lock into bake variables (`bake-args
<type>`), and shows what moved (`diff`). `cloud-build`, `cloud-deploy`, the
Makefile and the watch all call it. Nothing else parses the lock.

## m5 steps

### Group 1 `[exec]`: groundwork and the gate

#### s1 - Record m4's leftovers `[fast]` (done)

Add the run 42283134638 lines to gcp.md's "m4 results" (the android smoke VM's
checks and smoke tests, and `-next` passing and promoting for `linux` and `gis`).
Trim "Still open from m4" in the m5 handoff to gpu's live test only.

#### s2 - One helper for type facts `[exec]` (done)

Move `parent_of`, `toolsets_of`, `image_paths` and `rebuild_of` out of
`changed-targets.sh` into one sourced helper. The lock resolver needs a type's
toolsets, and `gcp_runner_settings` (D6) needs its rebuild class, so leaving them
there would mean copies (AGENTS.md, "One source per fact"). The class has to live
in `hooks/gcp/lib.sh`, because the orchestrator image carries that file for
`spawn-runner`. The Dockerfile parsing can live in a `scripts/cloud/` helper that
sources it. `test_changed_targets.py` must stay green unchanged. Fix r2.f5 (temp
repos inherit the host's git config) while in those tests.

#### s3 - Make the gate fail when it checked nothing (r2.f1, r2.f2) `[exec]` (done)

- `AGENT_TOOLSETS_DIR` overrides `record_dir` in `install-toolset.sh` and the
  readers (D7). Test that `record` installs `<name>.check` and `<name>.smoke.sh`
  and appends the name once.
- `agent-toolset-checks versions` fails when no toolset recorded a check.
- `cloud-build`'s smoke compares the image's `smoke-list` with the `smoke.sh`
  files of the toolsets the type installs (s2's helper), so a dropped record fails
  the pull request.
- r2.f2: one passing `smoke pr` case, with a skipped live test and exit 0
  asserted.
- Under AGENTS.md "Tests that can fail", each new test is shown red with its guard
  deleted (r2's mutations: `smoke.sh:smoke` at `install-toolset.sh:64`, and
  `failed=1` in the live-skip branch), and the step's commit says so.

Done as `scripts/cloud/record-check.sh`, which `make cloud-smoke` runs first. It
reads the record through one `docker run`, using the image's own
`AGENT_TOOLSETS_DIR`. Eight mutations each turned a test red. D7 is only part
done: `install-toolset.sh` now honors `AGENT_TOOLSETS_DIR`, but the readers still
default the path themselves. Making `write-versions.sh` the one writer of
`versions.md` goes with s6 (the runner Dockerfile) and s9 (`provision.sh`).

**STOP: swap to `[deep]`** (Opus 5.5, high).

### Group 2 `[deep]`: prove the sources, then the resolver

#### s4 - Spike the resolvers on a hosted runner `[deep]` (done)

These need Docker and Homebrew, which this session lacks, so they run as a
throwaway job on the draft PR for A (opening it needs your yes; see the question
below). The job proves:

- `crane digest` through `mirror.gcr.io` for `library/ubuntu:24.04` and
  `nvidia/cuda:13.0.3-devel-ubuntu24.04`, without a pull.
- In each base at that digest, with `APT::Snapshot` set:
  - `apt-get update` works, and the CUDA image's NVIDIA source is fetched live
    with no error (as this session showed for Docker's);
  - `apt-get install --simulate` of the type's package union is the same at
    snapshot T and today when nothing moved, and differs when something did.
- `brew bundle list` plus `brew deps --union` plus `brew info --json=v2` on the
  runner's Homebrew, after tapping and trusting `lolay/tap`, gives the same
  formula set as the `brew list --versions` section of `linux`'s versions report.
  If it doesn't, the step says why (build-only dependencies, `if OS.mac?`) and
  what the resolver does about it.
- The rustup channel manifest, `tenv tf list-remote`, Claude's `latest` URL and
  the GitHub `releases/latest` redirects for tflint and VirtualGL each parse to a
  version.

The spike job is deleted when it's done. Findings go in the handoff and shape s5.

Findings (`m5-spike`, runs 1 to 8; the job is deleted):

- **Digests:** `docker buildx imagetools inspect <ref> --format '{{json .Manifest}}'`
  gives the index digest with no pull (`skopeo` agrees). A bare registry `HEAD`
  on mirror.gcr.io returns 405 without a token, so the resolver uses
  `imagetools` and no crane download. `crane` would add a download for nothing.
- **apt on CUDA:** `APT::Snapshot` in `apt.conf.d` works. The snapshot run
  resolved older versions than today's, and two snapshot runs matched. NVIDIA's
  repo is fetched live with no error. None of `linux-gpu`'s packages come from
  it today: every origin is `Ubuntu:24.04/*`. The CUDA image's entrypoint prints
  a banner on stdout, so the resolver runs `--entrypoint /bin/bash`.
- **apt on plain Ubuntu: certificates first.** The stock `ubuntu:24.04` image has
  no `ca-certificates`, and snapshot.ubuntu.com is HTTPS only (HTTP redirects).
  So `apt-get update` under a snapshot only warns ("No system certificates
  available"), and then no package resolves. The build has to bootstrap:
  install `ca-certificates` from the live archive, then write the snapshot
  config, then everything else. With that, all three bases pass: two snapshot
  runs resolve the same set, and the snapshot differs from today by 9 to 12
  packages (linux 276 vs 278, gis 239 vs 241, linux-gpu 233 vs 234). The
  bootstrap floats `ca-certificates` and, on plain Ubuntu, `openssl` (3.0.13
  `ubuntu3.16` today; the CUDA base keeps its own `ubuntu3.9`). That's one more
  entry for D3's list of what the lock records but can't pin, and s6 builds
  the base this way.
- **Homebrew (linux):** 21 top formulae, a closure of 69 lines without
  build-only dependencies (100 with them). Once `brew info` resolves the alias
  `python` to `python@3.15`, that's the image's 68 exactly, in names and
  versions (run 3, against m4's deploy report). So `brew deps --union` without
  build dependencies is the right closure. `brew trust --tap` exists (Homebrew
  7.0.11).
- **Sources:** Claude `latest` 2.1.319 and `stable` 2.1.311; Rust stable
  1.99.1 from `channel-rust-stable.toml`; tflint `v0.65.0`; VirtualGL `3.1.5`;
  `tenv tf list-remote --stable` ends at 1.17.1. The resolver reads
  HashiCorp's `index.json` directly, with no tenv on the runner.

#### s5 - The lock and its resolver `[deep]` (done)

`scripts/cloud/lock.sh`:

- `resolve <type>` writes a candidate lock in D1's format. Each value is resolved
  within the constraint declared where the dependency lives (gcp.md, "Pinning a
  dependency"):
  - the base: `BASE_IMAGE` in the bake file, where a `@sha256` means pinned;
  - apt: `name=version` in a `packages.txt`;
  - Claude: `ARG CLAUDE_VERSION` in the runner Dockerfile (`latest`, `stable` or a
    version);
  - Rust and Terraform: `RUST_TOOLCHAIN` in `rust/install.sh`, and
    `TERRAFORM_VERSION` and `TFLINT_VERSION` in `terraform/install.sh`;
  - VirtualGL: `VIRTUALGL_VERSION` in `gpu/install.sh`.

  The apt snapshot moves to now only when the resolved set differs from the
  locked snapshot's. The base Dockerfile's inline `sudo ca-certificates curl`
  moves into a `packages.txt` the resolver reads.
- `bake-args <type>` prints the bake variables, and fails on a missing or
  malformed lock rather than falling back to `latest`.
- `diff <old> <new>` prints what moved, the watch's title included (`lock(linux):
  claude 2.1.318, go 1.27.4, apt 3 packages`).
- Tests use fakes for `crane`, `docker run` (apt), `brew` and `curl` that reject
  unknown flags and subcommands (AGENTS.md "Test fakes"; r2.f4 shows how they
  slip). Every guard (pinned skips, malformed lock, unchanged snapshot) is
  mutation-checked.

s5 as built: the line format is D1's, with `-` as the name for the
single-valued kinds (`base - <ref>@<digest>`, not `base ubuntu`), and
`release <name> <tag>` keeps GitHub's tag as is (`v0.65.0`). The base's apt
list is `images/cloud/base/packages.txt`, and the bake file now declares
`BASE_IMAGE` for `base` (the Dockerfile has no default left). `resolve` runs
on Linux only, since Homebrew resolves dependencies for the OS it runs on. The
apt change check compares today's resolution with the lock's apt lines, so it
runs one container per type instead of two. `tests/unit/test_lock.py` has 25
tests, and the resolver's eleven guards each go red when deleted.

**STOP: swap to `[exec]`** (Sonnet 5.5, high).

### Group 3 `[exec]`: the build consumes the lock

#### s6 - Build from the lock `[exec]` (done)

- The base Dockerfile writes the snapshot config from `ARG APT_SNAPSHOT`, and the
  runner stage removes it last (D5).
- Each pinned toolset's `ARG` sits right before its own layer
  (`RUST_TOOLCHAIN`, `TERRAFORM_VERSION`, `TFLINT_VERSION`, `VIRTUALGL_VERSION`),
  so a Rust bump rebuilds Rust and what follows, not the apt layers.
- The install scripts keep `stable` and `latest` as defaults, so the Packer and
  LXD android builds behave as before.
- `bake-args` feeds `cloud-build.yml`, `cloud-deploy.yml`'s `types` job and the
  Makefile. The deploy's `plan` job keeps its `curl …/latest` for the orchestrator
  only (D8). r2.f14 is folded in: `make cloud-build` builds `versions-<type>` and
  passes `COMMIT`, and the pull request job calls it.
- Gotcha: `base` is one bake target shared by `linux` and `gis`, so with
  different locks their Actions cache scope (`base`) would thrash. Scope it per
  type.
- `test_bake.py` covers the per-type variables.

#### s7 - Seed the locks `[exec]` (done)

Resolve and commit `images/cloud/{linux,gis,linux-gpu}/versions.lock` from the
draft PR's runner (or `make cloud-lock` on a host with Docker). The PR's
`cloud-build` then builds every type from its lock. Check each type's versions
report against its lock: the apt list matches exactly, and the Homebrew set
matches, versions aside (s4).

#### s8 - `make cloud-lock` and the scheduled rebuilds `[exec]` (done)

- `make cloud-lock TYPE=<type>` resolves locally and shows the diff, writing
  nothing unless `WRITE=1`. Makefile.md gets a row.
- The scheduled rebuilds build from the lock on `main`, already true after s6. A
  test pins it, so a schedule can't drift back to `latest`.

#### s9 - android's report to the summary (D2) `[exec]` (done on `main`, #52)

Packer downloads `/etc/agent-images/versions.md` (`provisioner "file"`,
`direction = "download"`). `vm-images` appends it to the summary and uploads it as
`versions-android-<image>.md` for 90 days, matching `types`. While in this code,
fix r2.f10's install split, so the LXD android image gets the runner, the skill
and the report too: move them into `install-packages.sh`, or into one helper the
runner Dockerfile also calls.

PR A opens here, after a walkthrough. **STOP: PR A waits for your review and
merge.** The first push deploy after it builds every type from its lock.

**STOP: swap to `[deep]`** (Opus 5.5, high).

### Group 4 `[deep]`: the watch

#### s10 - `lock-watch.yml`, the bot and the gate `[deep]` (done)

- **Triggers:** `cron: '17 8-22 * * *'` for the `nightly` class and `47 4 * * 0`
  for all types (D4), both `timezone: America/Los_Angeles`, plus
  `workflow_dispatch`. Concurrency is one run at a time, queued.
- **Resolving and guarding:** it resolves each type in the class with `lock.sh`.
  Before anything is pushed, it fails unless every changed path matches
  `images/cloud/*/versions.lock`.
- **Token:** `actions/create-github-app-token` with `permission-contents: write`
  and `permission-pull-requests: write`, in the `lock-bot` environment.
- **Branch and PR:** reset `lock/watch` to `main`'s head, commit with
  `createCommitOnBranch` (signed by GitHub), open the pull request or refresh the
  open one with the title from `lock.sh diff`, and turn on auto-merge (squash).
  An unchanged candidate ends the run green with nothing pushed.
- **Summary:** each pin with upstream's current version and its issue's state,
  read with `GITHUB_TOKEN` (the App has no Issues permission).
- **`cloud-build` gets a `gate` job** (`needs: [check, build]`, `if: always()`,
  fails unless both succeeded). The ruleset requires that one name, which stays
  stable as types come and go; the matrix job names don't.
- **Tests:** unit tests cover the path guard, the title and the
  pinned-issue summary. `actionlint` covers the workflow.

s10 as built (`scripts/cloud/lock-watch.sh`, `tests/unit/test_lock_watch.py`,
31 tests, 13 guards shown red when deleted):

- **Two jobs.** `resolve` (read-only `GITHUB_TOKEN`) resolves, decides and writes
  the pins summary; `propose` (environment `lock-bot`) gets only the moved locks,
  as a job output, so the App's key never shares a runner with the base images and
  apt runs pulled from upstream. `propose` runs the path guard itself, on every
  file it was handed, before any GitHub call.
- **The token is minted in the script,** not by `actions/create-github-app-token`:
  this session can't read other repositories to pin an action by SHA, and 30 lines
  of `openssl` and `curl` keep the key out of a third-party action. It's narrowed
  to this repository with Contents and Pull requests write, and revoked at the end.
- **A staging branch.** The plan's "reset `lock/watch` to `main`" would mark the
  open pull request merged (its head would be in `main`). The commit goes on
  `lock/watch-staging` at `main`'s head, then `lock/watch` moves to it.
- **One pull request across both schedules.** The hourly run carries the open
  pull request's locks for types it didn't resolve; a run whose candidate the open
  pull request already proposes on `main`'s head pushes nothing (no hourly rebuild
  of a red candidate); a run where nothing moved closes an open one.
- **`cloud-build` runs on every pull request.** With the trigger's `paths` filter,
  a pull request touching only `specs/` would never get the required `gate`. A
  `changes` job applies the same paths, and `gate` passes when nothing relevant
  changed.
- `lock.sh pins <type>` reads the pins with upstream's newest (it holds the
  upstream URLs); `lock-watch.sh pins` adds the issue state.
- `actionlint` (v1.7.12, through `go run` in `make cloud-lint`) covers the cloud
  workflows and `lock-watch.yml`; three summary printfs needed SC2016 notes.

#### s11 - Claude's self-update off on `nightly` types (D6) `[exec]` (done)

`gcp_runner_settings` emits `CLAUDE_UPGRADE=false` when the type's class is
`nightly`. `test_job_spec.py` asserts it both ways: on for `linux`, absent for
`gis`. A test with the class helper deleted goes red.

Done: `gcp_runner_settings <type>` (callers `job-spec.sh` and `spawn-runner` pass
the type). Three mutations (class read replaced, always emitted, never emitted)
each go red.

#### s12 - Docs `[fast]` (done)

Update these to match what was built:

- gcp.md: "The lock" (format, snapshot only during the build, NVIDIA floating, the
  pin locations), "Watch" (the weekly run, the gate job), "The lock bot" and
  "Rebuild cadence" ("Self-updates until m5's watch" becomes "Locked").
- The m5 section, with its results.
- The r2.f12 lines m5 touches: `cloud-build.yml`'s header and gcp.md:254-257, both
  still calling the check build-only.
- `design.md`, if its "track the latest" section names the watch, and the m5
  handoff.

**STOP: your setup.** These steps are GitHub and Terraform settings, outside the
repo:

1. Create the GitHub App `lolay-agent-images-lock-bot` with Contents (write), Pull
   requests (write) and Metadata (read), no Workflows permission, installed on
   `lolay/agent-images` only.
2. Create the `lock-bot` environment with deployment branches set to `main` only,
   and the App's id and private key as its secrets (`LOCK_BOT_APP_ID`,
   `LOCK_BOT_PRIVATE_KEY`).
3. Turn on "Allow auto-merge" in the repo settings.
4. In `main`'s ruleset, require the `gate` status check from `cloud-build`, and
   check that required approvals are 0. A required review would hold every lock
   pull request for you, which defeats the point. If you want reviews on human
   PRs, that needs a second rule.
5. In `lolay/infrastructure`, after reading the cleanup policy's dry-run log, set
   `cleanup_policy_dry_run = false` (`stacks/gcp/agents`).

PR B opens after a walkthrough. **STOP: PR B waits for your review and merge.**

#### s13 - Verify on `main` `[deep]` (in progress)

Results so far are under m5 in gcp.md: seven bot lock PRs merged themselves on
`gate` by 2026-11-10, two of them Claude releases (#63, #64), and the weekly run
moved `gis` and `linux-gpu` (#59). Still open: the closed-issue pin, the red
candidate, a session's `CLAUDE_UPGRADE=false`, the cleanup policy's live run,
and the cause of one failed watch run (42673647532).


- A manual `lock-watch` dispatch opens a pull request, `gate` goes green, it
  merges itself, and the push deploy promotes `linux` from the new lock.
- A pin with a closed issue shows in the summary.
- A deliberately broken candidate (a scratch pull request pinning a version that
  fails a smoke test) stays open and red, and the next watch run refreshes the
  same pull request rather than opening another.
- A new `linux` session shows Claude at the lock's version, with no upgrade at
  start (`CLAUDE_UPGRADE=false` in its environment).
- The cleanup policy's first live run keeps two versions per image.

Results go under m5 in gcp.md. Then the milestone review of `main` (r3), before m6
is planned.

## Not in m5

- gpu's live smoke test: still waiting on `linux-gpu`'s quota (m3 s6).
- r2's other lows (f6 to f9, f13, f15) and r1's open findings, except where a step
  above touches their code. They stay on lolay/agent-images#51 and #40.
- An android lock (D2), `pixi.lock` (D3), and the orchestrator's lock (D8).
