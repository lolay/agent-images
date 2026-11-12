# r2 incremental review: findings

- Kind: incremental
- Range: 506a1b9b2f0e31ecf435a509c8c3e904362f70b3..28ca834be3ded803eb35eff51be0bdc3f0d99b06
- Repo: lolay/agent-images
- Follows: r1 (full, at 506a1b9); covers m4 (lolay/agent-images#48) and #38, #41 to #47
- Reviewers: correctness (/code-review high), drift, tests, duplication, fix review; Opus, high, fresh context
- Verifier: dropped 4 of 21, merged 1, lowered 4 (Opus, fresh context)
- Accepted, not raised again: none

## tests

### r2.f1 - An empty toolset record passes every gate, and nothing tests the code that writes it

- Severity: medium
- Dimension: tests
- Kind: fix
- Location: `images/toolsets/install-toolset.sh:64` at 28ca834 (with `install-toolset.sh:32`, `images/toolsets/toolset-checks.sh:42-66`, `images/cloud/entrypoint.sh:161-170`)
- Evidence: `record()` is the only link from `images/toolsets/<name>/check.txt` and `smoke.sh` to `/etc/agent-images/toolsets/<name>.check` and `<name>.smoke.sh`, and `record_dir` is a readonly `/etc/...` path with no override (:32), so no test runs it. Downstream, a `toolsets.txt` whose toolsets have no `.check` or `.smoke.sh` files makes `agent-toolset-checks versions` and `smoke-list` exit 0 with no output; the entrypoint then reports `smoke: pass binaries ()` and runs no smoke test. The pull request check, the `-next` smoke and `agent-gce-smoke` would all go green having checked nothing.
- Verified: changing `smoke.sh:smoke.sh` to `smoke.sh:smoke` at :64 leaves the full suite green (252 run, 1 skipped); a record with names but no files gives exit 0 and empty output from both subcommands.
- Recommendation: let `AGENT_TOOLSETS_DIR` override `record_dir` and test that `record` installs `<name>.check` and `<name>.smoke.sh` and appends the name once. Fail `versions` when no toolset recorded a check, or have the pull request smoke compare the image's `smoke-list` with the `smoke.sh` files of the toolsets the type installs.
- Status: fixed
- Resolution: lolay/agent-images#53 (`AGENT_TOOLSETS_DIR` override in install-toolset.sh, `versions` fails on an empty record) and #54 (`scripts/cloud/record-check.sh`, run first by `make cloud-smoke`); r3 broke each guard and the suite went red

### r2.f2 - No test drives `agent-cloud-entrypoint smoke` to a pass

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `tests/unit/test_entrypoint.py` (`test_pr_skips_live_tests_and_says_why`) at 28ca834 (with `images/cloud/entrypoint.sh:164-167,190-192`)
- Evidence: in the test harness the scratch (not a mount) and datasets (writable as root) checks always fail, so every smoke run there exits 1, and the `smoke pr` skip test doesn't assert an exit code. Whether a skipped live test keeps `smoke pr` green is untested.
- Verified: `failed=1` added to the live-skip branch, and `exit 0` changed to `exit 1` after "smoke passed", each leave test_entrypoint green (35 OK).
- Recommendation: add a passing case (fake `mountpoint`, `findmnt`, `dd`, a `touch` that fails on datasets) and assert `smoke pr` with a skipped live test exits 0 and logs "smoke passed".
- Status: fixed
- Resolution: lolay/agent-images#53: `test_pr_with_a_skipped_live_test_passes_when_everything_else_does`; r3's mutations (`failed=1` in the live-skip branch, `exit 1` after the pass) go red

### r2.f3 - `test_live_reads_only_the_header` passes without the header limit

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `tests/unit/test_toolset_checks.py:59-64` at 28ca834 (code at `images/toolsets/toolset-checks.sh:76`)
- Evidence: the fixture's body lines are `echo '# smoke: live (...)'`, which never match `^# smoke: live`, so the `1,10` range in the sed is never exercised.
- Verified: removing `1,10` from the sed leaves the suite green.
- Recommendation: put a real `# smoke: live (...)` comment after line 10 of the fixture.
- Status: fixed
- Resolution: lolay/agent-images#53: the fixture's real marker sits on line 13; dropping `1,10` goes red (r3)

### r2.f4 - The strict gcloud fake still accepts unknown subcommands

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `tests/unit/test_deploy.py:34-61` at 28ca834
- Evidence: the outer case refuses unknown groups (:60), but the inner `run jobs` and `compute instances` cases have no `*)` branch and `executions` never checks `$4`. The strictness was added in this range (#48); AGENTS.md "Test fakes" asks the fake to reject what gcloud would.
- Verified: the extracted fake exits 0 for `run jobs executions lst --job x`, `run jobs bogus` and `compute instances delete x`; only `run services list` is refused.
- Recommendation: add `*) echo "ERROR: (gcloud) unknown: $*" >&2; exit 2 ;;` to each inner case and require `$4 == list` for `executions`.
- Status: fixed
- Resolution: lolay/agent-images#53: every inner case refuses unknown subcommands and `executions` requires `list`; r3's four mutations in deploy.sh each go red

### r2.f5 - The unit suite's temp repos inherit the host's git config

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `tests/unit/test_changed_targets.py:62-64` at 28ca834 (with `tests/unit/test_bake.py:112-119`, added in range by #44)
- Evidence: both `git()` helpers run git with the host's environment and global config. On a host with `commit.gpgsign=true` (this cloud session's has, with an SSH signer), `git commit` in setUp exits 128 and the test errors.
- Verified: with `GIT_CONFIG_GLOBAL` pointing at `commit.gpgsign=true` and a missing signing key, test_changed_targets gave 49 run, 48 errors; the tests reviewer saw one such error in a full run.
- Recommendation: run the helpers with `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1` (or `-c commit.gpgsign=false`).
- Status: fixed
- Resolution: lolay/agent-images#53: `git_env()` in tests/unit/support.py, used by test_changed_targets, test_bake and test_lock_watch; with commit.gpgsign=true globally they pass, without it 52 errors (r3)

## correctness

### r2.f6 - A smoke test that ignores TERM exits 137, not the documented 124, and the group kill is untested

- Severity: low
- Dimension: correctness
- Kind: fix
- Location: `images/toolsets/toolset-checks.sh:92-98` at 28ca834 (with `:20-21`, `images/toolsets/skills/agent-toolset-checks/SKILL.md:79`, `tests/unit/test_toolset_checks.py:97-103`)
- Evidence: when `--kill-after` fires, GNU timeout sends KILL to its own process group, itself included, so `wait` returns 137; line 98 adds the "timed out after" line only for 124. The header and the skill say 124 means a timeout. The hung-test test asserts 124 and the output line but never that the TERM-ignoring child died.
- Verified: a script with `trap "" TERM` makes the runner exit 137 after 6 s with no "timed out" line. Deleting `kill -KILL -- "-$pid"` (:96) or `--kill-after=5` leaves the suite green; without :96 the child outlives the call. (`setsid` alone is redundant with timeout's own `setpgid`, not a gap.) The gate still fails in the 137 case, and no current smoke.sh ignores TERM, hence low.
- Recommendation: treat 137 after the limit as a timeout (or record the deadline and compare), and have the test write the child's PID and assert it's gone; add a case where the script itself ignores TERM.
- Status: fixed
- Resolution: lolay/agent-images#53: a 137 at or after the limit reads as 124, and the tests check the child is gone; removing the group kill, the conversion or `--kill-after` each go red (r3)

### r2.f7 - A failing `dpkg-query` ends the versions report and fails the image build

- Severity: low
- Dimension: correctness
- Kind: fix
- Location: `images/toolsets/write-versions.sh:63` at 28ca834 (with `images/cloud/runner/Dockerfile:74-76`, `images/android/gce/provision.sh:77-78`)
- Evidence: `packages="$(dpkg-query ... 2>&1)"` is a plain assignment under `set -e`, unlike `block()`, which records a failure and carries on. Both build sites pipe the report through `tee` under pipefail.
- Verified: with a `dpkg-query` that exits 2 the report has no apt section and exits 2; the Dockerfile's `bash -o pipefail -c '... | sudo tee'` and provision.sh's pipe then fail. No test covers it.
- Recommendation: give the apt section `block()`'s `|| status=$?` handling, and add a case beside `test_a_failing_section_is_kept_and_says_so`.
- Status: fixed
- Resolution: lolay/agent-images#53: write-versions.sh keeps the apt section on a failing dpkg-query, tested (r3)

### r2.f8 - gis's smoke test loses its own failure output

- Severity: low
- Dimension: correctness
- Kind: fix
- Location: `images/toolsets/gis/smoke.sh:21-24` at 28ca834
- Evidence: `out="$(IFS=,; python3 -c "import ..." 2>&1)"` is an assignment under `set -e`, so a failed import exits before `echo "$out"`, and the traceback it captured is lost. It's the only smoke.sh that captures `2>&1` into a plain assignment.
- Verified: with a fake `ogrinfo` and a `python3` that prints a traceback and exits 1, `agent-toolset-checks smoke gis --brief` prints nothing and exits 1: the gate reads `smoke: FAIL smoke-gis ()`.
- Recommendation: `out="$(...)" || { echo "$out"; echo "gis: importing the Python modules failed"; exit 1; }`.
- Status: fixed
- Resolution: lolay/agent-images#53: gis/smoke.sh prints the failed import's output; test_gis_smoke.py goes red without it (r3)

### r2.f9 - `DEPLOY=false` stops a compute type's orchestrator but not its deploy

- Severity: low
- Dimension: correctness
- Kind: fix
- Location: `scripts/cloud/deploy.sh:70-74,148` at 28ca834 (with `images/cloud/orchestrator/supervisor.sh:102-117`)
- Evidence: `deploys()` is checked only in `cmd_type`, the Cloud Run path; `cmd_vm_build` and `cmd_vm_promote` don't read it. The supervisor drops any type with `DEPLOY=false`, compute included. A compute type with `DEPLOY=false` would be built, smoked and promoted with no orchestrator to serve it. The flag also has two readers, each with its own `true` default (`deploys()` and the supervisor's `type_setting`): more evidence for r1.f33.
- Verified: confirmed by reading; latent, since no compute env file sets `DEPLOY` and gcp.md:274 defines it for jobs.
- Recommendation: either honor `DEPLOY=false` in the vm deploy path too, or refuse it for `BACKEND=compute` (in deploy.sh and the supervisor), and read it through one `gcp_type_setting` helper (r1.f33).
- Status: fixed
- Resolution: lolay/agent-images#53: deploy.sh's `load_compute_type` and the supervisor both refuse DEPLOY=false on a compute type, each tested. The supervisor's refusal opened a new gap, see r3.f2

## drift

### r2.f10 - Only the Packer build gives the android image m4's runner, skill and report

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `images/android/gce/provision.sh:58-65,73-78` at 28ca834 (with `images/android/scripts/install-packages.sh`, `scripts/linux/build-image.sh`, `images/cloud/runner/Dockerfile:46-50`, `images/android/files/claude/CLAUDE.md`)
- Evidence: `agent-toolset-checks`, the `/agent-toolset-checks` skill and `versions.md` are installed in the android image only by provision.sh, which runs only under Packer. `install-packages.sh`, shared by Packer, the LXD build and `android-image-smoke.yml`, installs none of them, so the LXD android image lacks them, though gcp.md m4 (:1061-1062) says the runner is in "every runner image (Cloud Run and android)". The runner and skill install is written twice (the runner Dockerfile and provision.sh). The android session CLAUDE.md doesn't mention the report or the skill, which the cloud template now does (`images/cloud/runner/claude/CLAUDE.md:19-20`). The workflow's hand-listed checks are r1.f27's remaining part, not this finding.
- Verified: confirmed; merged with the android CLAUDE.md finding (same fix site).
- Recommendation: move the runner, skill and report install into `install-packages.sh` (or one shared helper the Dockerfile also calls), and add the cloud template's line to the android CLAUDE.md.
- Status: fixed
- Resolution: lolay/agent-images#52: install-packages.sh installs agent-toolset-checks, the skills and the report for every android driver (Packer, the LXD build, android-image-smoke.yml)

### r2.f11 - The android versions report never reaches a deploy summary or artifact, though three docs say every image's does

- Severity: low
- Dimension: drift
- Kind: decision
- Location: `.github/workflows/cloud-deploy.yml:251-298` (the `vm-images` job) at 28ca834 (with `specs/design.md:39-41`, `specs/gcp.md:482-486`, `specs/handoffs/handoff-m5-dependency-watch.md:16-19`)
- Evidence: the `vm-images` job has no report or artifact step; provision.sh only tees the report into the Packer log. m4's own design (gcp.md:1097-1098, :1103-1106) matches the code, but design.md, gcp.md's "Keep one image" section and the m5 handoff say every image's report lands in its deploy's summary and a 90-day artifact. m5's lock covers android.
- Verified: confirmed; a conflict between documents rather than a code bug.
- Recommendation: (a) download the report from the Packer build (`provisioner "file" { direction = "download" }`) and give `vm-images` the same summary and artifact steps as `types` (recommended, since m5 reads these reports), or (b) narrow the three passages to the Cloud Run types.
- Status: fixed
- Resolution: decided by the Owner on lolay/agent-images#51 (2026-11-06: the same summary and 90-day artifact as the Cloud Run types), done by #52: Packer downloads the report, vm-build fails without it, `vm-images` publishes it; seen in deploy runs 42795325876 and 43153015507 (r3)

### r2.f12 - Specs, headers and eleven `check.txt` files still describe the pre-m4 smoke

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `specs/gcp.md:254-257,403-407,867-869` at 28ca834 (with `.github/workflows/cloud-build.yml:1-4`, `scripts/cloud/deploy.sh:15-16`, `images/toolsets/{core,native,jvm,node,python,go,rust,proto,terraform,android-go,android-rust}/check.txt:1-2`)
- Evidence: gcp.md:254-257 and the cloud-build.yml header call the pull request check build-only; it now loads, smokes and reports. gcp.md:403-407 lists the `-next` smoke's pre-m4 checks and has gpu running `eglinfo` "against Xvfb" (`gpu/smoke.sh` uses `-p surfaceless`). gcp.md:867-869 and deploy.sh:15-16 describe agent-gce-smoke's old hand list. Nine `check.txt` headers say "Smoke checks (images/cloud/entrypoint.sh)" and two say "Smoke checks:", where m4 renamed them binary checks read by toolset-checks.sh; only display, gis, gpu and android were updated.
- Verified: confirmed; eleven headers, not ten as first reported.
- Recommendation: point those gcp.md passages at m4's "Binary checks and smoke tests", update the two script headers, and replace each `check.txt` header with a pointer to toolset-checks.sh (the format is already in its header and install-toolset.sh's) plus any toolset-specific note.
- Status: fixed
- Resolution: lolay/agent-images#52 and #54: gcp.md, the cloud-build and deploy.sh headers and all fifteen check.txt headers now describe the m4 checks

## duplication

### r2.f13 - write-versions.sh copies gis's install root

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `images/toolsets/write-versions.sh:23,59-61` at 28ca834 (with `images/toolsets/gis/install.sh:13`)
- Evidence: `AGENT_GIS_MANIFEST` defaults to `/opt/gis/pixi.toml`, a copy of `readonly root=/opt/gis` in gis/install.sh. Behind the `[[ -f "$gis_manifest" ]]` guard, moving the root drops the pixi section silently. (The per-tool sections themselves are what gcp.md m4 specifies.)
- Verified: confirmed, narrowed to the path copy.
- Recommendation: record the manifest path when gis installs (a recorded file, or a `profile.sh` variable) and read it from there.
- Status: fixed
- Resolution: lolay/agent-images#53: the gis profile exports GIS_MANIFEST and write-versions.sh reads it; no /opt/gis copy is left

### r2.f14 - No make target builds the versions report the pull request check shows

- Severity: info
- Dimension: duplication
- Kind: fix
- Location: `.github/workflows/cloud-build.yml:89-101` at 28ca834 (with `Makefile:309-310`, `.github/workflows/cloud-deploy.yml:221-245`)
- Evidence: the pull request check calls `docker buildx bake --load <t> versions-<t>` directly with `COMMIT`; `make cloud-build` builds no `versions-<t>` and passes no `COMMIT`, so a local build can't reproduce the report. The two workflows' report steps are near-copies, but differ for reasons (the deploy names the digest).
- Verified: lowered to info; Makefile.md:199 describes the pull request check correctly.
- Recommendation: have `make cloud-build` add `versions-$(TYPE)` for runner types and pass `COMMIT ?= $(shell git rev-parse HEAD)`, and let the pull request job call it.
- Status: fixed
- Resolution: lolay/agent-images#52 and #54: `make cloud-build` runs scripts/cloud/bake.sh, which builds `versions-<type>` too, and the pull request check calls it

### r2.f15 - The entrypoint keeps its own retire-margin default and guard

- Severity: info
- Dimension: duplication
- Kind: fix
- Location: `images/cloud/entrypoint.sh:39,205-206` at 28ca834 (with `hooks/gcp/lib.sh:52-58`, `scripts/cloud/job-spec.sh:60-63,108`)
- Evidence: #45 moved the 900 s default and the margin check into `gcp_retire_margin`, and job-spec always writes `AGENT_RETIRE_MARGIN_SEC`; the entrypoint still defaults it to 900 and repeats the guard. The image doesn't carry lib.sh, so it can't call the helper. Left over from r1.f19.
- Verified: lowered to info: the default is never used in production.
- Recommendation: `${AGENT_RETIRE_MARGIN_SEC:?}` when `AGENT_TASK_TIMEOUT_SEC` is set; keep the guard as an assertion.
- Status: fixed
- Resolution: lolay/agent-images#53: entrypoint.sh requires AGENT_RETIRE_MARGIN_SEC with the task timeout and keeps no default of its own

### r2.f16 - The report and record paths are spelled out in several places

- Severity: info
- Dimension: duplication
- Kind: decision
- Location: `images/cloud/runner/Dockerfile:74` and `images/android/gce/provision.sh:78` at 28ca834 (with `images/cloud/entrypoint.sh:43`, SKILL.md, the cloud CLAUDE.md; the record dir in `toolset-checks.sh:27`, `write-claude-md.sh`, both profiles)
- Evidence: `/etc/agent-images/versions.md` has two writers (each build site's `tee`), and `/etc/agent-images/toolsets` has a default in each reader. Sessions need the paths spelled out in prose, so some copies are unavoidable.
- Verified: lowered to info. install-toolset.sh's missing override is part of r2.f1.
- Recommendation: decide whether the paths are a contract with one definition. If so, have write-versions.sh write the report itself (one writer), and read the record dir from `AGENT_TOOLSETS_DIR` set once in the profiles.
- Status: accepted
- Reason: the report and record paths stay as they are. Sessions need them spelled out in prose anyway, and the writer duplication isn't worth a change (Owner, 2026-11-06)
- Resolution: lolay/agent-images#51, decision comment of 2026-11-06

## Dropped by the verifier

- The android smoke VM's 1500 s budget: 8 smoke tests run in seconds (Cloud Run's 9 take 7 s; boots are 27 s cold, 6 s warm per gcp.md m2), and the log-at-end design predates m4.
- cloud-build's `plan` job checking out by tag: already recorded in r1.f15's resolution and r1's status; Renovate's `pinGitHubActionDigests` pins it.
- A bake matrix for the per-type targets: test_bake.py catches a missing or wrong target, and a matrix would copy the type list out of `infra/gcp`.
- The skill's "120 s" and deploy.sh's "twelve at that limit": the largest type runs 10 smoke tests, so the bound holds; the 124 wording is r2.f6.
