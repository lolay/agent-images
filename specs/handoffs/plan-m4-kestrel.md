# Plan: m4, binary checks, smoke tests and the versions report

Status: s1 to s3 done 2026-11-06, green on lolay/agent-images#48 at `385a84f`.
Waiting on Gary's review of s3 before s4 (the remaining smoke tests, by Sonnet
subagents). To resume: check out this branch, read this plan and the PR.
Branch `claude/bold-albattani-fg54u5` (assigned by the harness), from `main` at
`68115e6`.

The design is [specs/gcp.md](../gcp.md) "m4 - Functional smoke and versions report";
state and gotchas are in [handoff-m4-functional-smoke.md](handoff-m4-functional-smoke.md).
This plan changes that design in the ways under "The rule" and revises gcp.md m4 to
match in s1. Since the handoff: hosted runners have about 44 GB free after "Free
disk"; `linux-gpu` is `DEPLOY=false` until its quota lands (lolay/agent-images#47);
r1.f27 and r1.f8 overlap this work.

## The rule (Gary, 2026-11-06)

- **Binary check**: a toolset's `check.txt` line, `label: command`. Proves the tool
  is installed and on `PATH` and prints its version. Nothing more: no device, no
  display, no build. Runs everywhere an image is checked.
- **Smoke test**: a toolset's `smoke.sh`. The least real work that shows the
  toolchain works (Go builds and runs hello; CUDA runs a kernel on the GPU). One
  per toolset at most, where the binary check isn't enough.
- A smoke test runs in the pull request when that's cheap and easy on a GitHub
  hosted runner. One that needs what the runner lacks (a GPU) says so in a header
  line, `# smoke: live (needs a GPU)`, and the pull request skips it with a
  `skip` line, never a `pass`.
- Every smoke test can run live in the image: in the deploy's smoke on GCP, and by
  hand in a session (`agent-toolset-checks smoke gpu`).
- Toolchains stay offline (no module, crate, package or provider fetches): the
  image has to work from what it holds. Fixture data a test needs that's too large
  for a heredoc is fetched at run time from a URL in the script, never baked into
  the image. None of the tests below needs any.

## Where each thing runs

| | Pull request (`cloud-build.yml`) | Deploy from `main` (`cloud-deploy.yml`) | A session |
| --- | --- | --- | --- |
| Where | GitHub hosted runner, no GCP access | Build and push on GitHub; the checks run in GCP | The live image |
| Builds | Every runner type, loaded into the runner's Docker | Changed types, pushed to Artifact Registry; android by Packer in GCP | |
| Binary checks | All, in the loaded image (`make cloud-smoke`) | All, in the `agent-<type>-next` Cloud Run execution; android on its smoke VM | `agent-toolset-checks versions` |
| Smoke tests | All but `live` ones | All, on the type's real hardware (the L4 for `linux-gpu` once it deploys) | `agent-toolset-checks smoke <name>` |
| Mounts | Local volumes stand in | The job's real scratch, outputs, datasets | Real |
| Versions report | PR summary and artifact | Run summary and 90-day artifact; android in the Packer log | `/etc/agent-images/versions.md` |
| On failure | PR is red, naming the check | Production keeps its image | |

android isn't built on pull requests at all (Packer runs only in GCP), so its
checks and smoke tests first run on the deploy's smoke VM. Until `linux-gpu`'s quota
lands, its GPU smoke test runs only by hand in a session.

## Smoke tests

Minimal, per the rule. Each is one file, at most about 20 lines, fixtures in
heredocs.

| Toolset | Smoke test | Where |
| --- | --- | --- |
| native | C hello built with `cmake` and `ninja`, then run | PR |
| jvm | `javac` and `java` a Hello class | PR |
| node | `node` runs a script | PR |
| python | `uv venv` on the installed Python, run a script in it | PR |
| go | `go build` hello, run it | PR |
| rust | `cargo build` hello, run it | PR |
| proto | `protoc` a one-message file to a descriptor set | PR |
| terraform | `terraform init` and `validate` on a module with no providers | PR |
| display | Screenshot of the virtual display (moved from `check.txt`) | PR |
| gis | `ogrinfo` on a generated GeoJSON; the Python bindings import (moved from `check.txt`) | PR |
| gpu | `nvidia-smi`, `agent-cuda-probe` runs its kernel, the driver libraries load (moved from `check.txt`); EGL and Vulkan reported, not failed | live |
| android-go, android-rust | Cross-compile hello for arm64 with the NDK | deploy smoke VM |

None for `core` and `browser` (binary checks are enough), or `android` (the smoke
VM's emulator boot is its smoke test already).

`check.txt` files lose the lines that do work: display's `display`, `opengl` and
`screenshot`; gpu's `gpu`, `cuda`, `driver-libs`, `egl` and `vulkan` (`nvcc` and
`virtualgl` stay, and `agent-cuda-probe` gets a `command -v`); gis's `bindings` and
`one gdal`. So every binary check passes on a hosted runner, `linux-gpu` included,
with no special mode.

## Shape

Creates:
- `images/toolsets/toolset-checks.sh`, installed as `/usr/local/bin/agent-toolset-checks`
  in the cloud and android images: `versions` (the binary checks, today's
  `toolchains` loop moved out of the entrypoint), `smoke-list`, and `smoke <name>`
  (fresh temp directory, own process group, `timeout 120`, the offline variables),
  and `live <name>`, which the entrypoint's `smoke pr` uses to skip `live` tests.
  One runner for both images, so the timeout, temp directory, offline settings and
  the `live` rule live once (AGENTS.md "One source per fact", r1.f27).
- `images/toolsets/write-versions.sh`: the versions report.
- `images/toolsets/<name>/smoke.sh` for the twelve toolsets in the table.
- `tests/unit/test_toolset_checks.py`, `tests/unit/test_write_versions.py`.
- `images/shared/claude/skills/agent-toolset-checks/SKILL.md`: the session skill (s3).

Modifies:
- `images/toolsets/install-toolset.sh` (records `smoke.sh` as `<name>.smoke.sh`)
- `images/toolsets/{display,gpu,gis}/check.txt` (work moves to `smoke.sh`)
- `images/cloud/entrypoint.sh` (binary checks and smoke tests through the runner;
  `smoke pr`; names the report)
- `images/cloud/runner/Dockerfile`, `images/cloud/docker-bake.hcl`
- `images/android/gce/provision.sh`, `images/android/gce/files/agent-gce-smoke.sh`,
  `images/android/scripts/setup-user.sh` (installs the skill)
- `scripts/cloud/changed-targets.sh`, `scripts/cloud/deploy.sh`
- `.github/workflows/cloud-build.yml`, `.github/workflows/cloud-deploy.yml`
- `Makefile` (`cloud-smoke` passes `pr`), `Makefile.md`
- `tests/unit/test_entrypoint.py`, `test_bake.py`, `test_changed_targets.py`,
  `test_deploy.py` (its fake `gcloud` rejects flags it doesn't know)
- `images/cloud/runner/claude/CLAUDE.md`, `images/android/files/claude/CLAUDE.md`
  (the report, and running a smoke test live)
- `specs/gcp.md` (m4 rewritten to this rule, the deploy table row, m4 results),
  `specs/image-toolsets.md` (binary checks and smoke tests in the toolset files)

## Decisions (mine, for Gary to overrule)

1. **One runner, most of r1.f27.** The entrypoint and `agent-gce-smoke` each loop
   over the runner with their own line format, so the android VM now runs every
   binary check (android-go's and android-rust's have never run) and its smoke
   tests; its hand-listed `cargo-ndk` and `gomobile` lines go. KVM, emulator,
   display service and `sudo` stay bespoke. Left open in f27: the PATH and profile
   assembly, and `android-image-smoke.yml`'s hand list. The PR says "partly
   addresses r1.f27".
2. **The deploy runs every smoke test, `live` ones included.** It's already on the
   type's hardware and already paying for the execution; a `live` test there costs
   seconds. Say if you'd rather `live` meant "sessions only".
3. **The report reuses the runner** for each binary check's version line. With the
   work moved out of `check.txt`, they all pass at build time.
4. **`versions-<type>` as a Dockerfile stage** (`FROM scratch`, the one file), one
   bake target per type like `<type>-tools`, which `test_bake.py` enforces. Bake's
   `matrix` would need a second copy of the type list.
5. **r1.f8 fixed**: `--task-timeout=1800s` on the smoke execution (twelve tests at
   the 120 s cap is under 25 min worst case; `gcloud run jobs execute --help`
   confirms the flag) and `timeout-minutes: 120` on the deploy's `types` job.
6. **Load everything, measure, then decide.** With 44 GB free, `gis` and
   `linux-gpu` try `--load`; only a type the draft PR shows can't fit falls back to
   the no-output build (binary checks and smoke tests then run only at deploy),
   with the numbers in the workflow comment.
7. **No consolidation step.** The parent writes s1 and s2 and reviews every s3
   script, and the s3 files are disjoint.

## Steps

--- WAVE 1 [deep] ---

#### s1 - [deep] Binary checks and smoke tests: the framework

Rewrite gcp.md m4 to the rule above (terms, where each runs, the trimmed table)
and image-toolsets.md's toolset files. `toolset-checks.sh` with its subcommands and
the `live` header; `install-toolset.sh` records `smoke.sh`; the entrypoint's
`smoke` runs `versions`, then one `smoke-<name>` check per test (`smoke: pass
smoke-go (go: built and ran hello)`, the detail being the test's last line), and
`smoke pr` skips `live` ones; `agent-gce-smoke` runs both; the runner Dockerfile and
`provision.sh` install the runner; the `check.txt` lines that do work move out (the
display, gpu and gis smoke tests are written here with them, so nothing is lost
between commits); `changed-targets.sh` and gcp.md's "every runner type" row take
`toolset-checks.sh`; r1.f8. `cloud-build.yml` loads each runner target (not
`orchestrator`) and runs `make cloud-smoke TYPE=<target>`; its job timeout goes
from 45 to 60 minutes. Tests: the runner (pass, fail, hang past a shortened
`AGENT_SMOKE_TIMEOUT_SEC`, no test, `live` skipped with `--pr` and run without it,
temp directory removed, offline variables set) with the real `timeout` and
`mktemp`; the entrypoint's smoke lines. Install shellcheck, shfmt and packer in
the session first, so `make cloud-lint cloud-test` runs here. Commit, push, open
the draft PR, and record `df` after each load from its run.

#### s2 - [deep] Versions report

`write-versions.sh` writes `/etc/agent-images/versions.md`: type, build date and
commit (a bake arg, which s3's issues quote),
Claude's version, the binary checks' version lines, `brew list --versions`,
`rustup toolchain list`, `tenv tf list`, `pixi list` for `/opt/gis`, and
`dpkg-query` in a collapsed `<details>`; a section whose tool isn't there is left
out; progress on stdout. The runner Dockerfile's last `RUN`, after Claude, with
`AGENT_TYPE` from bake; `provision.sh` writes it last and prints it to the Packer
log. Both workflows build `versions-<type>` with `type=local` in the same bake
call, append it to `$GITHUB_STEP_SUMMARY` under the commit (and the digest on
deploy), and upload `versions-<type>-<tag>.md` with `retention-days: 90`. Risk to
check on the draft PR: bake-action's `push: true` may override the report's local
output; if so the deploy sets `<type>.output=type=registry` itself, and the PR's
bake call takes the same shape so `main` runs what the PR proved. Commit and push.

#### s3 - [deep] The `/agent-toolset-checks` session skill

A Claude Code skill baked into every runner image, so it exists only in sessions on
them: `images/shared/claude/skills/agent-toolset-checks/SKILL.md`, installed into
`~/.claude/skills/` by the runner Dockerfile (cloud) and `setup-user.sh` (android),
from the one source. It drives `agent-toolset-checks`; it never parses `check.txt`
or runs a test itself.

1. **Ask what to run**, with the choice dialog: everything (binary checks and every
   smoke test, `live` ones included), binary checks only, or chosen smoke tests.
   The choices come from `agent-toolset-checks smoke-list` in the image, not a list
   in the skill.
2. **Run and report**: one line per check, as the deploy smoke prints them, and the
   versions report's path.
3. **On a failure, ask before filing.** Run the failed one again once, to tell a
   flake from a break, then ask: file an issue on lolay/agent-images, or don't. A
   no files nothing.
4. **File it.** First look for an open issue with the same title and comment there
   instead. If the session's GitHub tools reach lolay/agent-images, create it there.
   If they don't (a session scoped to another repo), print a prefilled
   `issues/new?title=&body=` link and the body to paste. It never files without the
   yes.

**What the issue holds** (the rule lives in `SKILL.md`, its one source):

- Title: `<type>: <check> fails` (`linux: smoke-go fails`, `gis: binary check gdal fails`).
- The image: type, build date and commit from the versions report's header (s2
  adds the commit, as a bake arg), and the image digest or android image name
  where the session can see it.
- What failed: binary check or smoke test, the toolset, the exact command to rerun
  (`agent-toolset-checks smoke go`), exit status or timeout, and whether the rerun
  failed too.
- The output: the check's last 50 lines, in a code block.
- The versions: only the lines for that toolset's tools from `versions.md`, not
  the whole report.
- Nothing from the user's own work: no project files, environment dumps, tokens or
  paths outside the temp directory. The skill shows the body before filing.

Labels: `toolset-check`, which s3 creates in the repo once, plus the toolset's
name when that label exists (the skill adds none of its own). Test: a unit test that
every `agent-toolset-checks` subcommand the skill names exists in the runner, so
the two can't drift.

--- STOP: tier change [deep] -> [exec] ---
  Gary reviews s1 to s3 on the draft PR (its run shows the loads, the disk, the
  binary checks, the display, gpu and gis smoke tests, and the reports) before
  the rest fan out. The parent stays on Opus and dispatches Sonnet subagents.
---

--- WAVE 2 [exec] ---

#### s4 - [exec] The other smoke tests

Sonnet subagents, at most six at once, each given the contract (gcp.md m4 as s1
rewrites it) plus the toolset's files and the git rule (write the one file; don't
commit, push or switch branches): native, jvm, node, python, go, rust; then proto,
terraform, android-go, android-rust. This session has cc, cmake, ninja, java, node,
uv, go and cargo, so the first six run here; the agent says which it couldn't run.
Prompt notes: python's `uv venv` gets `--python "$(command -v python3)"` and
`UV_PYTHON_DOWNLOADS=never`; the android ones use `$ANDROID_NDK_HOME`'s clang.
The parent reviews each against the contract, runs `make cloud-lint cloud-test`,
and commits and pushes each batch so the draft PR checks it in the images.

--- STOP: tier change [exec] -> [deep] ---
---

--- WAVE 3 [deep] ---

#### s5 - [deep] Verify on images

On the draft PR: each loaded type shows its binary checks, a `smoke-<name>` pass
line per PR smoke test, a `skip` line for gpu, and a versions report. A scratch
commit with `exit 1` in one test turns that type red naming it; revert it. Write
"m4 results" in gcp.md (disk after each load, check time per type, any type left on
the no-output build). Then ask Gary to open the PR for review.

After merge, not provable in the PR: the first deploy's `-next` lines in Cloud Run
(linux, gis), the android binary checks and smoke tests on the smoke VM at the next
weekly deploy or a manual `cloud-deploy` dispatch with `android` (about 45 min,
$0.50, Gary's call), and the gpu smoke test live in a `linux-gpu` session or at
`-next` once the quota lands. Those go in a follow-up to the m4 results and the m5
handoff (`Review: pending, after m4's PR merges`). This plan and the m4 handoff
leave the branch, promoted to gcp.md, as its last commit.

## Progress

- [x] s1, green on lolay/agent-images#48 at `e29d598`. Measured on the hosted
  runner (145 GB disk, `--load`): free after the load 95 GB (linux), 89 GB (gis),
  78 GB (linux-gpu: 13.8 GB of images, 16.5 GB of build cache), so every type keeps
  `--load`. Job time: linux 9 min, gis 10.5 min, linux-gpu 14 min, orchestrator
  2 min. The smoke itself takes 3 s (linux) to 8 s (gis). Two fixes the run found:
  `make cloud-smoke` had never run in CI and Docker refuses a read-only anonymous
  volume (now a read-only tmpfs); `Xvfb -version` prints no version line (now
  `command -v`).
- [x] s2, green at `0415692`: reports of 508 lines (linux, 370 apt packages) and
  similar for gis and linux-gpu, uploaded as `versions-<type>-pr48-<sha>.md`. `--push` and `--load` leave versions-<type>'s
  local output alone (`bake --print` shows it; `test_bake.py` checks it), so the
  deploy keeps `push: true` and the risk in s2 didn't happen.
- [x] s3, green at `385a84f`. The skill's one source is
  `images/toolsets/skills/agent-toolset-checks/SKILL.md`, installed where the
  runner is (the cloud runner Dockerfile, android's provision.sh), not on the Mac
  or the LXD android image. The `toolset-check` label doesn't exist on
  lolay/agent-images yet; the skill adds it only if it does.
- [ ] s4
- [ ] s5
