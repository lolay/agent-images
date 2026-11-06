# r1 full review: findings

- Kind: full
- Range: ..506a1b9b2f0e31ecf435a509c8c3e904362f70b3
- Repo: lolay/agent-images
- Follows: none (first review; covers m1, m2 and m3's first PR, lolay/agent-images#37)
- Reviewers: architecture, contracts, security, tests, dependencies, duplication, hotspots (top 10 by commits x lines); Opus, xhigh, fresh context
- Verifier: dropped 0 of 34; lowered 19 (Opus, xhigh)
- Accepted, not raised again: none

## architecture

### r1.f1 - Linux runners never run `claude update`: the shared runner takes the Homebrew cask branch whenever `brew` exists

- Severity: high
- Dimension: architecture
- Kind: fix
- Location: `images/shared/runners/claude.sh:75-89` at 506a1b9
- Evidence: the upgrade is `CLAUDE_UPGRADE != true` → skip; `elif command -v brew` → `brew upgrade --cask claude-code@latest || warn "claude upgrade failed; continuing"`; else `claude update`. Since toolsets s3 the `core` toolset installs Homebrew on every Linux image, and it's on the runner's PATH: Cloud Run through `images/cloud/runner/Dockerfile:51` and `profile.sh:44`, GCE and LXD through `agent-runner.service:34` (`bash -lc`) and `images/android/files/profile.sh:12`. Claude is installed with the native installer (`runner/Dockerfile:56`, `android/scripts/install-packages.sh:22`), whose `~/.local/bin` comes first on PATH. So the cask upgrade fails, warns, and `claude update` is never reached. Breaks gcp.md 201-206 and 389-397: the weekly types (`gis`, `android`, `linux-gpu`) run a Claude up to 7 days old.
- Verified: reproduced with a fake `brew` (exit 1) and a fake `claude` on PATH: the log shows `brew upgrade --cask claude-code@latest`, then `claude --version` and `claude self-hosted-runner`; `claude update` is never called.
- Recommendation: choose by platform, not by whether brew exists (`[[ "$(uname -s)" == Darwin ]]`, as the watchdog already does), and add a unit test that runs claude.sh on Linux with brew on PATH and asserts `claude update` runs.
- Status: fixed
- Resolution: lolay/agent-images#45: claude.sh chooses the cask upgrade by `uname -s` (Darwin), else `claude update`; covered by test_shared_runner.py

### r1.f2 - The set of runner types is declared in four unlinked places; the PR check can't see a new type

- Severity: medium
- Dimension: architecture
- Kind: fix
- Location: `.github/workflows/cloud-build.yml:51-54` at 506a1b9
- Evidence: types are `infra/gcp/*.env` (`supervisor.sh:62`, `changed-targets.sh:45`); their build graph is `docker-bake.hcl` targets and default group, and again `changed-targets.sh` parsing `FROM agent-<x>`; the PR build is a hard-coded `target: [linux, linux-gpu, gis, orchestrator]`. A PR adding `infra/gcp/3d-gpu.env` and `images/cloud/3d-gpu/` passes CI without the image being built; the deploy's bake then fails on `main` (and through r1.f3 holds back the orchestrator). gcp.md:93-94 leaves the bake targets out of "adding a type".
- Verified: `grep bake tests/unit` is empty; nothing cross-checks the bake file against the env files.
- Recommendation: generate the PR build matrix from `changed-targets.sh --all` as the deploy does, and add a unit test that `docker buildx bake --print <type>` resolves for every `BACKEND=run` env file. Update gcp.md's checklist.
- Status: fixed
- Resolution: lolay/agent-images#44: cloud-build's matrix comes from `make cloud-targets` (the BACKEND=run env files); test_bake.py checks every type has its bake targets

### r1.f3 - One failing type holds back orchestrator deploys for every type

- Severity: low
- Dimension: architecture
- Kind: decision
- Location: `.github/workflows/cloud-deploy.yml:265-273` at 506a1b9
- Evidence: the orchestrator job runs only when `types` and `vm-images` both succeeded or were skipped; the matrices are `fail-fast: false`, so one failed leg fails the result, and `plan` diffs against the last fully successful push, so every later push re-plans the failing type and skips the orchestrator again. Hook, supervisor and env changes can't reach the working types until it's fixed.
- Verified: deliberate (comment at :265-267, "the next push retries"); `workflow_dispatch targets=orchestrator` is an escape hatch. Lowered from medium.
- Recommendation: options: (a) the supervisor starts a type's orchestrator only once its job or image family exists, so the orchestrator job depends only on `plan` (recommended); (b) wait only for types added in this diff; (c) keep as is and document the dispatch escape hatch.
- Status: open

### r1.f4 - The cloud CLAUDE.md template tells `linux-gpu` sessions there's no GPU

- Severity: low
- Dimension: architecture
- Kind: fix
- Location: `images/cloud/runner/claude/CLAUDE.md:3` at 506a1b9
- Evidence: "(Ubuntu 24.04, x86_64, no GPU)" is rendered into every type; `images/toolsets/display/CLAUDE.md:1-2` says "without a GPU"; on `linux-gpu` both sit beside `images/toolsets/gpu/CLAUDE.md` ("One NVIDIA GPU"). The template should hold only facts true of every type (image-toolsets.md:370-376).
- Verified: confirmed in all three files.
- Recommendation: drop "no GPU" from the template; say "software OpenGL (Mesa)" in the display fragment instead of "without a GPU".
- Status: open

## hotspots

### r1.f5 - The cloud smoke's I/O checks can't fail, so a broken outputs or datasets mount is promoted

- Severity: medium
- Dimension: hotspots
- Kind: fix
- Location: `images/cloud/entrypoint.sh:88` (with `:108`, `:113-121`, `:124`) at 506a1b9
- Evidence: `check` runs each probe as `if detail="$("$@" 2>&1)"; then`; inside an `if` condition bash turns off `errexit` for the functions it calls, so only a probe's last command decides. `outputs_round_trip` ends with an `echo`, `scratch_disk`'s `dd` and `datasets_read_only`'s `ls` are mid-function. `deploy.sh:141-148` promotes on a passing smoke.
- Verified: reproduced with the entrypoint test harness: `AGENT_OUTPUTS_ROOT=/dev/null/broken` prints `smoke: pass outputs (mkdir: cannot create directory '/dev/null': Not a directory; …; wrote, read and deleted …)`; a missing `AGENT_DATASETS` passes `datasets` too.
- Recommendation: chain every step (`mkdir -p "$dir" && printf … >"$file" && [[ "$(cat "$file")" == smoke ]] || return 1`), and the same for `dd` and `ls`; add tests with an unwritable outputs root and a missing datasets mount.
- Status: fixed
- Resolution: lolay/agent-images#41: every I/O step in scratch_disk, outputs_round_trip and datasets_read_only returns 1 on failure; tests cover an unwritable outputs root and a missing datasets mount

### r1.f6 - Rebuilding the same commit on the same UTC day deletes the live android image

- Severity: medium
- Dimension: hotspots
- Kind: fix
- Location: `scripts/cloud/deploy.sh:168-172` (with `.github/workflows/cloud-deploy.yml:249`, `deploy.sh:249-259`) at 506a1b9
- Evidence: the image is named `agent-$TYPE-$(date -u +%Y%m%d)-${GITHUB_SHA::12}` and built with `packer build -force`; `vm-promote` keeps one image per type, so the live image carries that name. A same-day dispatch, the Sunday schedule after a same-day push deploy, or a rerun makes `-force` delete it at image creation, leaving family `agent-android` empty through capture and smoke (up to about 55 min), and for good if the smoke fails. `spawn-runner:176` then gets 404, which exits 2: sessions fail without retry.
- Verified: the googlecompute plugin's `-force` deletes by name and project only (`step_check_existing_image.go`, `step_create_image.go`); the family is never checked.
- Recommendation: make the name unique per run (add `${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}` or a UTC time), or refuse to build when the named image is the family's live image.
- Status: fixed
- Resolution: lolay/agent-images#42: the image name adds `$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT`, and vm-build no longer uses `-force`

### r1.f7 - Powered-off VMs of a removed or renamed compute type are never deleted

- Severity: low
- Dimension: hotspots
- Kind: fix
- Location: `scripts/cloud/deploy.sh:298-320` (with `images/cloud/orchestrator/supervisor.sh:102-105`, `scripts/cloud/changed-targets.sh:51`) at 506a1b9
- Evidence: the reaper covers only compute types baked into the orchestrator; `cmd_prune` deletes jobs and images but not instances; `git diff --diff-filter=D` reports a rename as R, so renaming an env file triggers no prune.
- Verified: rename reproduced (`R100`, no D). Running VMs are still capped by `maxRunDuration` with DELETE; what leaks is TERMINATED VMs and their 100 GB disks.
- Recommendation: have `cmd_prune` delete `managed-by=agent-images` instances whose type has no env file; pass `--no-renames` to the `git diff` calls.
- Status: open

### r1.f8 - A hung deploy smoke can bill for 9.5 hours

- Severity: low
- Dimension: hotspots
- Kind: fix
- Location: `scripts/cloud/deploy.sh:143-144` (with `scripts/cloud/job-spec.sh:95`) at 506a1b9
- Evidence: the smoke runs on `agent-<type>-next` with the production task timeout (34200 s); toolset checks have no timeouts; the `types` job has no `timeout-minutes`, and cancelling `gcloud … --wait` leaves the execution running. On `linux-gpu` that's an L4.
- Verified: confirmed.
- Recommendation: pass `--task-timeout=900s` to the smoke execution and set `timeout-minutes` on the `types` job.
- Status: fixed
- Resolution: lolay/agent-images#48: the smoke execution runs with `--task-timeout=1800s` (deploy.sh `DEPLOY_SMOKE_TASK_TIMEOUT_SEC`; the job has `maxRetries: 0`), the `types` job has `timeout-minutes: 120`, and each smoke test has its own 120 s limit; test_deploy.py pins the flag. Not yet seen in a real deploy (r2)

## tests

### r1.f9 - The shared runner scripts every platform runs have no tests

- Severity: medium
- Dimension: tests
- Kind: fix
- Location: `images/shared/agent-runner.sh:43-57`, `images/shared/runners/claude.sh:107-152`, `images/shared/agent-runner-watchdog.sh` at 506a1b9
- Evidence: no file in `tests/unit` runs them (`test_entrypoint.py` swaps in fakes). They hold the EPHEMERAL power-off, the runner.env → CLI flag mapping (`--retire-at`, `--exit-if-unused-min`, `--kill-session-after-min`) and the upgrade path where r1.f1 lives. Each link of the settings chain (job spec, entrypoint, hook runner-env, GCE order) is tested against hand-written values, so a rename on one side keeps every suite green.
- Verified: `grep` of tests/unit finds no reference. A GCE shutdown regression is bounded by `maxRunDuration` (about 9.5 h); Tart and LXD `runner-once` wait for power-off with no cap. Lowered from high: a coverage gap, though r1.f1 is a live bug in exactly this code.
- Recommendation: add `test_shared_runner.py`: claude.sh with a fake `claude` recording argv over a runner.env matrix (including brew on PATH); agent-runner.sh with `EPHEMERAL=true` and a fake `sudo`; the watchdog's second-failure rule; and two chain tests (job spec → entrypoint → claude.sh argv; hook metadata → agent-gce-order → claude.sh argv).
- Status: fixed
- Resolution: lolay/agent-images#45: tests/unit/test_shared_runner.py (26 tests) runs claude.sh, agent-runner.sh and the watchdog, with chain tests from the job spec and the hook

### r1.f10 - The orchestrator is promoted with no smoke, and its test's fake CLI accepts any flag

- Severity: medium
- Dimension: tests
- Kind: decision
- Location: `.github/workflows/cloud-deploy.yml:268-322`, `scripts/cloud/deploy.sh:275-284`, `images/cloud/orchestrator/supervisor.sh:216-223` at 506a1b9
- Evidence: every type gets a smoke before promotion; the orchestrator, which serves all of them, goes from bake to the host's metadata key, nightly, with the latest Claude. `test_supervisor.py:22-37`'s fake `claude` accepts any arguments. A Claude release that drops or renames a flag the supervisor passes (the CLI already lists `--pool-secret-file` as a deprecated alias) makes every orchestrator exit at start, restarted forever, with no check failing.
- Verified: confirmed; the type smoke also checks only `claude --version`, so a dropped runner flag isn't caught either.
- Recommendation: options: (a) before `deploy.sh orchestrator`, run the pushed image's `claude self-hosted-runner orchestrator --help` and fail unless every flag the supervisor passes is listed, with the flag list in one place the unit test reads (recommended); (b) a full smoke that starts the supervisor against a test environment; (c) accept the risk while Claude self-updates are watched by hand.
- Status: open

### r1.f11 - Deploy promotion's operation-error path is untested

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `scripts/cloud/deploy.sh:71-86` (with `tests/unit/fake_gcp.py:166-167`) at 506a1b9
- Evidence: the fake's Run operations always finish with no error. With the `.error` check gone, a failed `-next` write would promote an image that was never smoked.
- Verified: reproduced: replacing `deploy.sh:83` with `if false; then` leaves all 32 deploy tests passing. Lowered from medium: the code is right today.
- Recommendation: give the fake an operation-error hook for Run and test that a failed `-next` write stops before the smoke and that a failed live write fails the deploy.
- Status: open

### r1.f12 - The unit suite runs only on path-filtered PRs, and `make test`'s help says otherwise

- Severity: low
- Dimension: tests
- Kind: decision
- Location: `Makefile:188-191`, `.github/workflows/ci.yml:5-33`, `.github/workflows/cloud-build.yml:7-21` at 506a1b9
- Evidence: `ci.yml` (push and PR) runs `make ci` = lint + `test`, and `test` is only `packer validate` though its help says "and run helper unit tests". The 179 tests run only in `cloud-build.yml` on PRs, behind a path filter; `cloud-deploy` has no test step.
- Verified: the path filter covers every source the tests exercise, so every PR touching tested code runs them; the gap is direct pushes and the help text. Lowered from medium; fix → decision.
- Recommendation: fix the help text; decide whether `cloud-deploy` runs `make cloud-test` first (about 75 s) and whether `cloud-build`'s `check` job drops its path filter so it can be required.
- Status: open

### r1.f13 - Tart and LXD host runner scripts have no tests

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `hooks/spawn-runner`, `hooks/linux/spawn-runner`, `scripts/runner-once.sh`, `scripts/linux/runner-once.sh:76-78`, `scripts/lib.sh:211-232`, `scripts/linux/lib.sh:73-92` at 506a1b9
- Evidence: they carry the same exit 0/1/2 contract, redelivery check, claim release and reclaim as the tested GCP hook. LXD `runner-once` ends its wait on one failed `lxc info` (:76-78), then stops and deletes the still-running VM.
- Verified: no tests exist. Lowered from medium: `host-setup.sh:56` holds LXD refreshes, the main source of transient `lxc` failures.
- Recommendation: Python tests in `test_spawn_runner.py`'s style with fake `lxc`, `systemd-run`, `tart`, `launchctl` on PATH: redelivery exits 0 with no second submit, all slots busy exits 1, a failed submit releases the claim, SIGTERM stops and deletes the VM, reclaim spares a live pid.
- Status: open

### r1.f14 - The run backend reports success when the work order can't be read

- Severity: info
- Dimension: tests
- Kind: fix
- Location: `hooks/gcp/spawn-runner:114-125` at 506a1b9
- Evidence: the `:run` body is built in a process substitution, so a jq failure posts an empty body, logs "started", exits 0 and keeps the marker; the fake accepts any `:run` body.
- Verified: reproduced with a directory as the work-order file. Lowered to info: the orchestrator writes the file as the same uid and `:87` already rejects a missing or empty one.
- Recommendation: build the body into a variable first (`body="$(jq …)" || fail 1 …`) and make the fake reject a `:run` with no runner override.
- Status: open

## dependencies

### r1.f15 - Actions in the credentialed deploy jobs are pinned by tag, and nothing proposes bumps

- Severity: medium
- Dimension: dependencies
- Kind: decision
- Location: `.github/workflows/cloud-deploy.yml:129,133,242` (and every `uses:` in the file) at 506a1b9
- Evidence: `types`, `vm-images`, `orchestrator` and `prune` run with `environment: production` and `id-token: write`, on push and nightly and weekly schedules with no human, and use `google-github-actions/auth@v3.0.0`, `setup-gcloud@v3.0.1`, `hashicorp/setup-packer@v3.4.0`, `actions/checkout@v7.0.1`, `docker/*` by tag. A tag can be moved; the federated identity reaches `compute.instanceAdmin.v1`, `iam.serviceAccountUser` on agent-orchestrator (which reads every `claude-env-*`), Artifact Registry write and `run.developer`. No `.github/dependabot.yml` exists, though gcp.md:458 expects one; design.md:38-39 calls release tags "a supply-chain guard".
- Verified: every `uses:` is a tag; no Dependabot config. Not verified: which of these releases are marked immutable on GitHub.
- Recommendation: options: (a) pin every action in `cloud-deploy.yml` to a full commit SHA with the tag as a comment, and add Dependabot for `github-actions` (recommended); (b) pin only the non-immutable ones; (c) keep tags and correct design.md's claim.
- Status: fixed
- Resolution: lolay/agent-images#43: Gary chose SHA pins with Renovate (GitHub Actions only, weekly, 7-day minimum release age). One tag remains: `actions/checkout@v7.0.1` in cloud-build's new `plan` job (#44 landed after #43; no credentials there), which Renovate's digest pinning should pick up

### r1.f16 - Packer is installed at two versions across the workflows

- Severity: low
- Dimension: dependencies
- Kind: fix
- Location: `.github/workflows/ci.yml:20-22` at 506a1b9 (also `images/macos/plugins.pkr.hcl:2`, `triage.yaml:93`, `README.md:79,89` at 1.16.1; `cloud-build.yml:36-38`, `cloud-deploy.yml:242-244`, `images/android/gce/plugins.pkr.hcl:2` at 1.16.2)
- Evidence: the macOS gate installs 1.16.1, the cloud gate and deploy 1.16.2 (the newest release); build hosts get whatever Homebrew has.
- Verified: each workflow matches the template it validates, so nothing fails today. Lowered from medium.
- Recommendation: move everything to 1.16.2 and keep the version in one file all three workflows read.
- Status: open

### r1.f17 - shellcheck and shfmt lint the same scripts at two versions

- Severity: low
- Dimension: dependencies
- Kind: fix
- Location: `.github/workflows/cloud-build.yml:31-32` (with `ci.yml:23-24`, `Makefile:111-115`) at 506a1b9
- Evidence: `cloud-build` installs Ubuntu's (no `apt-get update` first), `ci` installs Homebrew's, unpinned; `SHELL_SCRIPTS` includes `CLOUD_SCRIPTS`, so both gate the cloud scripts. A formatting change in shfmt can pass one and fail the other.
- Verified: confirmed; "design.md says CI tools are pinned" was overstated (it pins Actions and Packer), but its rationale is what the unpinned tools break. Lowered from medium.
- Recommendation: install the same versions in both workflows from one source; add `apt-get update` if apt stays.
- Status: open

### r1.f18 - Ubuntu 24.04 is the base everywhere with no recorded reason

- Severity: info
- Dimension: dependencies
- Kind: decision
- Location: `images/cloud/base/Dockerfile:15` at 506a1b9 (also the orchestrator Dockerfile, `images/android/gce/build.pkr.hcl:20`, `scripts/linux/build-image.sh:29`)
- Evidence: the policy is track latest, pin on breakage; `ubuntu:latest` is 26.04. The real constraint is `base-gpu`: CUDA 13.0 (Cloud Run's driver) has no ubuntu26.04 image, and `linux-gpu` is `linux`'s Dockerfile.
- Verified: no rationale recorded anywhere. Not verified: the CUDA 26.04 tag list.
- Recommendation: record the constraint beside `ARG BASE_IMAGE` and in gcp.md's choices, with what lifts it; decide separately whether the orchestrator, GCE and LXD images follow.
- Status: open

## contracts

### r1.f19 - GCP runner settings reach runner.env by two paths with different defaults

- Severity: low
- Dimension: contracts
- Kind: fix
- Location: `hooks/gcp/spawn-runner:136-144` at 506a1b9 (with `scripts/cloud/job-spec.sh:76-80`, `images/cloud/entrypoint.sh:199-206`, `images/shared/runners/claude.sh:56-58`)
- Evidence: the compute hook writes `${RELEASE_IDLE_SESSION_MIN:-}` and `${KILL_SESSION_AFTER_MIN:-}`, so an unset key is empty, which claude.sh reads as "never"; the Cloud Run job uses `-60` and `-480`. The compute retire time has no future-time guard; the entrypoint's does.
- Verified: latent: `android.env` sets every key. The "no `AGENT_RETIRE_AT`" part was dropped: its only reader isn't on the android image. Lowered from medium.
- Recommendation: one `gcp_runner_settings <type>` helper in `hooks/gcp/lib.sh` with one set of defaults and the retire guard, used by `job-spec.sh` and the hook.
- Status: fixed
- Resolution: lolay/agent-images#45: `gcp_runner_settings` in hooks/gcp/lib.sh, used by job-spec.sh and the hook, with one set of defaults

### r1.f20 - android.env's build keys don't trigger an image rebuild

- Severity: low
- Dimension: contracts
- Kind: decision
- Location: `scripts/cloud/changed-targets.sh:107-119` (with `scripts/cloud/deploy.sh:169-172`) at 506a1b9
- Evidence: `DISK_GB` and `IMAGE_FAMILY` go to Packer (the image's disk size is every VM's minimum), but an env change redeploys only the orchestrator (by design, gcp.md:262). Lowering `DISK_GB` makes every insert fail with 400 → exit 1 → endless retry until the weekly rebuild; renaming the family gives 404 → exit 2.
- Verified: confirmed. Lowered from medium: rare operator edits, loud failures.
- Recommendation: options: rebuild the image when those keys change, or decouple them (a fixed minimum disk for the image, a family derived from the type, the hook refusing a `DISK_GB` below the image's).
- Status: open

### r1.f21 - No GCP type can pin Claude, though gcp.md describes how

- Severity: low
- Dimension: contracts
- Kind: decision
- Location: `images/cloud/entrypoint.sh:228-235` (with `scripts/cloud/job-spec.sh:101-110`, `.github/workflows/cloud-deploy.yml:108-110`) at 506a1b9
- Evidence: the entrypoint copies `CLAUDE_UPGRADE`, `CLAUDE_USE_GIT_PROXY` and five more into runner.env if set, but nothing sets them: the job's env is a fixed list, the hook overrides only the secret and label, and no env-file key maps to them. The deploy always resolves `latest`. gcp.md:472's pin procedure ("set the runner's `CLAUDE_UPGRADE=false`") has nothing behind it.
- Verified: confirmed; the pin table is framed around m4's lock, not built yet. Lowered from medium. Because of r1.f1, an image-level pin would hold on Linux today.
- Recommendation: define the runner settings an env file may carry (with r1.f19's helper), add a `CLAUDE_VERSION` input to the deploy, and drop pass-through names outside that contract; or leave it to m4 and say so in gcp.md.
- Status: open

### r1.f22 - A missing key in a type's env file retries forever instead of failing

- Severity: low
- Dimension: contracts
- Kind: fix
- Location: `hooks/gcp/spawn-runner:77,167-168` at 506a1b9
- Evidence: required keys are checked with `${X:?}`, which exits 127 at top level (1 inside a function); `on_exit` maps both to 1 (retry), though the header promises 2 for a broken setup.
- Verified: tested in bash 5.2. A missing env file still exits 2 (`:76`).
- Recommendation: `[[ -n ${X:-} ]] || fail 2 "X missing in infra/gcp/$RUNNER_TYPE.env"`.
- Status: open

## security

### r1.f23 - macOS runner VMs keep SSH on with admin/admin, unused after the build

- Severity: low
- Dimension: security
- Kind: decision
- Location: `images/macos/build.pkr.hcl:13-17` (with `scripts/runner-once.sh:73`) at 506a1b9
- Evidence: the Cirrus base's Remote Login with `admin`/`admin` and passwordless sudo stays on; runtime uses only `tart exec`; VMs run on Tart's default shared NAT, not Softnet.
- Verified: the reviewer's attack (a session SSHing into the sibling VM) doesn't hold: Tart's default NAT enables vmnet bridge isolation, which prevents cross-VM traffic (Softnet README). What remains is ARP spoofing on the shared bridge and SSH from the host. Lowered from high to low; fix → decision.
- Recommendation: options: disable Remote Login (or password auth) at the end of the Packer build, which costs nothing since nothing uses it (recommended); run with `--net-softnet`; or accept and note the ARP caveat in design.md:70.
- Status: open

### r1.f24 - Debug VMs get the orchestrator's environment key by default

- Severity: low
- Dimension: security
- Kind: decision
- Location: `scripts/vm-configure.sh:55-64` (and `scripts/linux/vm-configure.sh:54-64`) at 506a1b9
- Evidence: without `ENVIRONMENT_SECRET_FILE`, the VM gets `keychain_secret claude-environment-secret "$vm" default`, the same `default` item the orchestrator uses (`orchestrator-run.sh:35`), written to `~/.claude-runner/environment-secret` as the session user; the VM keeps serving queued sessions, any of which can read the key.
- Verified: confirmed; documented at design.md:153, which contradicts design.md:109 ("never enters a VM"). Lowered from medium: debug VMs are an operator's choice.
- Recommendation: drop the `default` fallback and require a per-VM item for a separate environment, or keep it and reconcile design.md.
- Status: open

### r1.f25 - Any session can overwrite or delete another session's outputs

- Severity: low
- Dimension: security
- Kind: decision
- Location: `scripts/cloud/job-spec.sh:121` at 506a1b9 (with gcp-infrastructure.md:130)
- Evidence: `agent-runner` has `storage.objectAdmin` on the outputs bucket and every execution mounts it read-write; the per-session prefix is a convention. gcp.md:755-759 accepts shared visibility for one owner, not integrity.
- Verified: confirmed.
- Recommendation: `objectCreator` + `objectViewer`, or soft delete or versioning on the bucket; or extend gcp.md's acceptance to integrity.
- Status: open

### r1.f26 - Per-type secret files in the orchestrator container don't isolate types

- Severity: info
- Dimension: security
- Kind: fix
- Location: `images/cloud/orchestrator/supervisor.sh:200-224` at 506a1b9
- Evidence: every orchestrator and hook runs as one uid with one service account that can read every `claude-env-*`, so a separate mode-600 file per type isn't a boundary. Harmless while every type is one owner's.
- Verified: confirmed; gcp.md doesn't claim isolation outright.
- Recommendation: say in gcp.md that the types share one trust domain, and that a type serving another audience needs its own container and service account.
- Status: open

## duplication

### r1.f27 - The toolset contract's readers exist only in the cloud image

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `images/android/gce/files/agent-gce-smoke.sh:43-61` at 506a1b9 (with `images/cloud/entrypoint.sh:143-168`, `.github/workflows/android-image-smoke.yml:56-79`, `images/android/files/profile.sh:16-20`)
- Evidence: every Linux image records each toolset's `*.check`, `*.path` and `*.profile.sh`, but only the cloud entrypoint runs the checks and only the cloud profile reads paths. The android image's gate hand-lists checks, the android smoke workflow hand-lists different ones, and `android-go`/`android-rust`'s `check.txt` never run anywhere.
- Verified: confirmed; the android `*.path` gap is latent (only `gpu` has a `path.txt`). Lowered from medium.
- Recommendation: move the check runner and the PATH/profile assembly into `images/toolsets/` and use them from the cloud entrypoint and profile, `agent-gce-smoke` and the workflow; keep only KVM and emulator checks bespoke.
- Status: open
- Resolution: partly lolay/agent-images#48: one runner, `images/toolsets/toolset-checks.sh` (`agent-toolset-checks`), now serves the cloud entrypoint, `agent-gce-smoke` and the session skill, so android-go's and android-rust's `check.txt` run on the android smoke VM (not yet observed). Still open: the PATH and profile assembly (`images/android/files/profile.sh` hard-codes PATH and ignores `*.path`), and `android-image-smoke.yml`, which still hand-lists its checks; r2.f3 covers the install sites (r2)

### r1.f28 - Toolset environment is half-migrated: `$JAVA_HOME` is promised but only Android sets it

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `images/toolsets/jvm/CLAUDE.md:1` at 506a1b9 (with `images/android/files/profile.sh:5-12`, `images/toolsets/android/install.sh:32-36`, `images/toolsets/android-go/install.sh:10-13`)
- Evidence: the jvm fragment says "OpenJDK 21 (`$JAVA_HOME`)", but the toolset has no `profile.sh` and nothing on the Cloud Run images exports it; the android profile hard-codes `JAVA_HOME`, the SDK and the cargo and go bins; two toolsets source the image's profile to get `ANDROID_HOME`.
- Verified: confirmed; overlaps r1.f27.
- Recommendation: move each fact into its toolset (`jvm/profile.sh` for `JAVA_HOME`, the SDK variables and paths into the android toolset's records, go and cargo bins into theirs).
- Status: open

### r1.f29 - The guest clears Claude's quarantine flag without the host's signature check

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `images/shared/runners/claude.sh:79-86` at 506a1b9 (with `scripts/lib.sh:98-112`)
- Evidence: the host verifies `codesign --verify --strict -R='anchor apple generic'` before `xattr -d`, and its comment says the guest does the same; the guest doesn't.
- Verified: confirmed. Lowered from medium: the guest is throwaway, and the host's requirement accepts any Developer ID, not Anthropic's team.
- Recommendation: one verify-then-clear helper with a team-ID requirement, used by both; or correct the comment.
- Status: open

### r1.f30 - macOS cleanup stops the VM after 30 s, though everything budgets 120 s to drain

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `scripts/runner-once.sh:40` at 506a1b9
- Evidence: `tart stop "$vm"` with Tart's default 30 s timeout, then SIGKILL; the Linux copy uses `lxc stop --timeout 120`, and the guest plist, host job and `orchestrator-service.sh` all budget 120 s.
- Verified: Tart's default confirmed in `Stop.swift`. Lowered from medium: only operator stops (`runner-stop`, `runner-uninstall`) are affected and the session requeues.
- Recommendation: `tart stop --timeout 120 "$vm"`.
- Status: open

### r1.f31 - The Android image's three build drivers disagree on the stderr gate

- Severity: low
- Dimension: duplication
- Kind: decision
- Location: `scripts/linux/build-image.sh:93-119` at 506a1b9 (with `images/android/gce/provision.sh:73-77`, `.github/workflows/android-image-smoke.yml`)
- Evidence: the LXD build publishes and moves the alias, then only logs "not green"; the Packer build exits 1; the smoke workflow fails the run. The staging layout is copied three times.
- Verified: the LXD behavior is documented (linux.md:119-126), so it's a deliberate difference. Lowered from medium.
- Recommendation: choose one policy (gate before `lxc publish`, or warn in Packer), and stage from one shared script or file list.
- Status: open

### r1.f32 - changed-targets' test fixture models an image layout that no longer exists

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `tests/unit/test_changed_targets.py:30,34,141-155` at 506a1b9 (with `scripts/cloud/changed-targets.sh:65-87`, gcp.md:152-153,165)
- Evidence: the fixture has an `images/cloud/base-gpu/` directory and `linux-gpu` `FROM agent-base-gpu`; the real tree has neither (base-gpu is a bake target, `linux-gpu` is `FROM agent-linux`). `test_a_types_directory_rebuilds_only_it` asserts a linux change rebuilds linux alone, the opposite of what the repo needs. `parent_of` stops at a missing directory, so the planned `3d-gpu` `FROM agent-base-gpu` would never reach `base`.
- Verified: on the real tree, base and linux changes rebuild `linux-gpu` correctly; the `3d-gpu` gap reproduces. Lowered from medium.
- Recommendation: rewrite the fixture to the real layout; before `3d-gpu`, teach `parent_of` that `base-gpu`'s parent is `base` (or read bake contexts); fix gcp.md:152-153,165.
- Status: fixed
- Resolution: lolay/agent-images#44: the fixture models the real layout, and `parent_of` maps base-gpu to base

### r1.f33 - Env files are parsed by sourcing in some places and by sed in others

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `scripts/cloud/changed-targets.sh:99-127` at 506a1b9 (with `deploy.sh:58-62`, `images/shared/agent-runner-watchdog.sh:25,39`)
- Evidence: `BACKEND` and `REBUILD` are read with `sed '^BACKEND=([a-z]+)'` in changed-targets and by sourcing in deploy, supervisor and the hook; the watchdog sources runner.env, then seds `WATCHDOG_ACTION`, while `entrypoint.sh:233` writes `%q`-quoted values. A quoted value splits them.
- Verified: confirmed, latent.
- Recommendation: one `gcp_type_setting` helper that sources in a subshell; the watchdog reads both values from one sourced subshell.
- Status: open

### r1.f34 - The Anthropic setup script copies linux's toolset list and isn't linted

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `scripts/anthropic/setup.sh:19` at 506a1b9 (with `Makefile:101-115`)
- Evidence: the hard-coded list equals base plus linux's Dockerfile toolsets today; a toolset added to linux would silently miss Anthropic's environment. `scripts/anthropic` is in neither lint glob.
- Verified: confirmed.
- Recommendation: derive the list from the Dockerfiles as `toolsets_of` does, and add `scripts/anthropic/*.sh` to `SHELL_SCRIPTS`.
- Status: open
