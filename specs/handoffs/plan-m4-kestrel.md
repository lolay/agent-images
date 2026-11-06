# Plan: m4, functional smoke and versions report

Status: plan written 2026-11-06, waiting on Gary's review. Nothing built yet.
Branch `claude/bold-albattani-fg54u5` (assigned by the harness), from `main` at
`68115e6`.

The design is [specs/gcp.md](../gcp.md) "m4 - Functional smoke and versions report";
state and gotchas are in [handoff-m4-functional-smoke.md](handoff-m4-functional-smoke.md).
This plan is how the steps get built, plus the decisions the spec leaves open.

Since the handoff: hosted runners have about 44 GB free after "Free disk" (not 14);
`linux-gpu` is `DEPLOY=false` until its quota lands (lolay/agent-images#47), so its
new smoke runs only in the pull request build; r1.f27 and r1.f8 overlap this work.

## Shape

Creates:
- `images/toolsets/toolset-checks.sh`: the one runner for recorded checks and smoke
  scripts, installed as `/usr/local/bin/agent-toolset-checks` in the cloud images
  and the android image. Two images read the record (cloud entrypoint,
  `agent-gce-smoke`), so the timeout, the temp directory and the offline
  environment live in one file (AGENTS.md "One source per fact", r1.f27).
- `images/toolsets/write-versions.sh`: the versions report (spec).
- `images/toolsets/<name>/smoke.sh` for core, native, jvm, node, python, go, rust,
  proto, terraform, gis, gpu, android, android-go, android-rust (spec).
- `tests/unit/test_toolset_checks.py`: the runner's own tests (pass, fail, hang past
  a shortened timeout, no script, temp directory removed, offline variables set).
- `tests/unit/test_write_versions.py`: sections present or left out by what's
  installed, against fakes that reject arguments the real tools wouldn't take.

Modifies:
- `images/toolsets/install-toolset.sh` (record `smoke.sh` as `<name>.smoke.sh`)
- `images/cloud/entrypoint.sh` (`toolchains` and the smoke scripts through the
  runner; names the report; `smoke functional`)
- `images/cloud/runner/Dockerfile` (install the runner; report as last layer; a
  `versions` stage)
- `images/cloud/docker-bake.hcl` (`AGENT_TYPE` arg and `versions-<type>` per type)
- `images/android/gce/provision.sh`, `images/android/gce/files/agent-gce-smoke.sh`
- `scripts/cloud/changed-targets.sh` (the two new scripts rebuild every type and VM)
- `scripts/cloud/deploy.sh` (`--task-timeout` on the smoke execution, r1.f8)
- `.github/workflows/cloud-build.yml`, `.github/workflows/cloud-deploy.yml`
- `Makefile` (`cloud-smoke` takes `SMOKE=functional`), `Makefile.md`
- `tests/unit/test_entrypoint.py`, `test_bake.py`, `test_changed_targets.py`,
  `test_deploy.py` (its fake `gcloud` learns to reject unknown flags)
- `images/cloud/runner/claude/CLAUDE.md` (where the report and the runner are)
- `specs/gcp.md` (the deploy table row, m4 results), `specs/image-toolsets.md`
  (`smoke.sh` in the toolset files)

## Decisions (mine, for Gary to overrule)

1. **One check runner for both images, closing most of r1.f27.**
   `agent-toolset-checks versions` is today's `toolchains` loop moved out of the
   entrypoint; `agent-toolset-checks smoke-list` prints the toolsets with a recorded
   script, in install order; `agent-toolset-checks smoke <name>` runs one in a fresh
   `mktemp -d` under `$TMPDIR`, removed after, under `timeout 120`, with the offline
   variables. The entrypoint and `agent-gce-smoke` each loop over `smoke-list` with
   their own `check` wrapper and line format, so the android VM now runs every
   `check.txt` too (android-go's and android-rust's have never run), and its
   hand-listed `cargo-ndk` and `gomobile` checks go, since those `check.txt` files
   cover them. KVM, emulator, display and `sudo` stay bespoke. Left open in r1.f27:
   the PATH and profile assembly, and `android-image-smoke.yml`'s hand list. The PR
   says "partly addresses r1.f27", not "closes". A session can run the runner too,
   which CLAUDE.md mentions. The narrow alternative (runner does smoke scripts only,
   `toolchains` stays in the entrypoint) is smaller and leaves f27 as it is.
2. **GPU types smoke `functional` on a hosted runner.** `linux-gpu`'s `check.txt`
   needs a device (`nvidia-smi`, `agent-cuda-probe`), so a full smoke can't pass
   without one. `agent-cloud-entrypoint smoke functional` runs `claude` and the
   smoke scripts only; `cloud-build.yml` passes it for a type whose env file sets
   `GPU_TYPE` (read from `infra/gcp/<type>.env`, not a list in the workflow). The
   full smoke still runs at `-next`, once `DEPLOY=false` goes.
3. **The `gpu` smoke compiles a heredoc kernel**, not `cuda-probe.cu`: the source
   isn't in the image, and the contract keeps fixtures in the script. The build
   already compiles `cuda-probe.cu`, so this checks `nvcc` on new source.
4. **The report reuses the runner**: `write-versions.sh` gets each check's label and
   version line from `agent-toolset-checks versions` rather than parsing `*.check`
   a second time. At build time a check that needs the runtime (the GPU, the
   display's screenshot) records its failure line as it is; the report says so in
   one line rather than hiding it.
5. **`versions-<type>` as a Dockerfile stage.** The runner Dockerfile's stages
   become `runner` and `versions` (`FROM scratch`, the one file). `_runner` sets
   `target = "runner"`; each `versions-<type>` inherits `<type>` with
   `target = "versions"` and no tags. `test_bake.py` learns the new target and the
   `AGENT_TYPE` arg. Explicit targets per type follow the existing pattern; bake's
   `matrix` would need the type list in the bake file, a second copy of
   `infra/gcp`.
6. **r1.f8 fixed here**: `gcloud run jobs execute ... --task-timeout=1800s` on the
   smoke (linux's nine scripts at the 120 s cap is 18 min worst case, against 9.5 h
   now; `gcloud run jobs execute --help` confirms the flag), and `timeout-minutes:
   120` on the deploy's `types` job. The PR says "closes r1.f8".
7. **Load everything first, measure, then decide.** With 44 GB free, `gis` and
   `linux-gpu` both try `--load` in the pull request. The spec's no-output fallback
   is taken only for a type the draft PR's run shows can't fit, with the numbers in
   the workflow comment.
8. **No consolidation step.** The parent writes s1 and s2 and reviews every s3
   script; the s3 files are disjoint, one per toolset. Add it back if the s1 and s2
   diff reads heavy at review.

## Steps

--- WAVE 1 [deep] ---

#### s1 - [deep] Smoke framework

`toolset-checks.sh` with the three subcommands; `install-toolset.sh` records
`smoke.sh` as `<name>.smoke.sh`; the entrypoint's `toolchains` calls `versions`, then
one `check smoke-<name>` per script (`smoke: pass smoke-go (go: built and ran hello,
vet clean)`, the detail being the script's last line), and `smoke functional`;
`agent-gce-smoke` runs `versions` and the scripts; the runner Dockerfile and
`provision.sh` install the runner; `changed-targets.sh` and gcp.md's "every runner
type" row take `toolset-checks.sh`; `deploy.sh` gets `--task-timeout` and the
`types` job its timeout (r1.f8), with the fake `gcloud` rejecting flags it doesn't
know. `cloud-build.yml` loads each runner target and runs `make cloud-smoke
TYPE=<target>` (`SMOKE=functional` for a GPU type), not `orchestrator`; its job
timeout goes from 45 to 60 minutes. Tests: `test_toolset_checks.py` and the
entrypoint's (a script that passes, fails, hangs past a shortened timeout via
`AGENT_SMOKE_TIMEOUT_SEC`, and a toolset without one), using the real `timeout`
and `mktemp`. Docs: image-toolsets.md's toolset files. The session has no
shellcheck, shfmt or packer: install them first, so `make cloud-lint cloud-test`
runs here. Commit, push, open the draft PR so `cloud-build` loads and smokes the
images (no scripts yet, so it proves the loading and the disk), and record `df`
after each load.

#### s2 - [deep] Versions report

`write-versions.sh` writes `/etc/agent-images/versions.md`: type and build date,
Claude's version, the `versions` lines, `brew list --versions`, `rustup toolchain
list`, `tenv tf list`, `pixi list` for `/opt/gis`, and `dpkg-query` in a collapsed
`<details>`, each section left out when its tool isn't there, progress on stdout.
The runner Dockerfile's last `RUN`, after Claude, with `AGENT_TYPE` from bake;
`provision.sh` writes it at the end and prints it to the Packer log. The bake
targets and `test_bake.py`. Both workflows build `versions-<type>` with
`type=local` in the same bake call, append it to `$GITHUB_STEP_SUMMARY` under the
commit and digest (the PR under the commit), and upload `versions-<type>-<tag>.md`
with `retention-days: 90`. The entrypoint's smoke names the file; CLAUDE.md
mentions it. Risk to check on the draft PR: bake-action's `push: true` sets every
target's output to the registry, which may override `versions-<type>`'s local
output. If it does, the deploy sets `<type>.output=type=registry` itself instead of
`push: true`; the pull request's bake call is written the same way, so the shape
that runs on `main` is the one the PR proved. Commit and push.

--- STOP: tier change [deep] -> [exec] ---
  Gary reviews s1 and s2 on the draft PR (its `cloud-build` run shows the load,
  the disk, the smoke and the versions report) before the scripts fan out.
  The parent stays on Opus and dispatches Sonnet subagents; no model swap here.
---

--- WAVE 2 [exec] ---

#### s3 - [exec] Per-toolset smoke scripts

Sonnet subagents, at most six at once, each given gcp.md m4 s3's contract verbatim
plus the files to read and the git rule (write the one file, don't commit, push or
switch branches). Wave 2a: core, native, jvm, node, python, go. Wave 2b: rust,
proto, terraform. Wave 2c: gis, gpu, android, android-go, android-rust. This session
has git, jq, age, cc, cmake, ninja, java, node, pnpm, python3, uv, ruff, go and
cargo, so most of 2a and 2b can run their script here; protoc, buf, terraform,
tflint, sops, GDAL, CUDA and the NDK can't, and the agent says so. Notes for the
prompts, from reading the toolsets: rust installs `clippy` and `rustfmt`
(`--profile minimal --component clippy,rustfmt`); terraform's `tflint` is a plain
binary, offline without plugins; python's `uv venv` gets `--python "$(command -v
python3)"` and `UV_PYTHON_DOWNLOADS=never`; android's `sdkmanager` gets
`--offline`. The parent reviews each script against the contract, runs `make
cloud-lint cloud-test`, and commits and pushes per sub-wave (`m4.s3 core to go
smoke scripts`), so the draft PR's run checks each batch in the images.

--- STOP: tier change [exec] -> [deep] ---
---

--- WAVE 3 [deep] ---

#### s4 - [deep] Verify on images

On the draft PR: each loaded type shows a `smoke-<name>` pass line per toolset and a
versions report in its summary. A scratch commit with `exit 1` in one script turns
that type's check red naming the toolset; revert it. Write "m4 results" under the
milestone in gcp.md (disk after each load, smoke time per type, report size, any
type left on the no-output build). Then ask Gary to open the PR for review.

What the PR can't show waits for `main`: the first deploy's `-next` lines in Cloud
Run (linux, gis), and the android three on the smoke VM at the next weekly deploy
(Sunday 05:37 Pacific) or a manual `cloud-deploy` dispatch with `android` (about
45 min, $0.50; Gary's call). Those go in a short follow-up to the m4 results, and
in the m5 handoff, which this session writes last, with `Review: pending, after
m4's PR merges`. This plan and the m4 handoff leave the branch, promoted to gcp.md,
as its last commit before merge.

## Progress

- [ ] s1
- [ ] s2
- [ ] s3 (2a, 2b, 2c)
- [ ] s4
