# Plan: m1 `linux` on GCP

Status: **approved 2026-11-05; running s1 to s12 unattended, stopping at s13.** `MIN_IDLE=0` chosen. Branch
`claude/eloquent-mayer-ivkml8` in `lolay/agent-images`; `lolay/infrastructure` needs
no change for m1 (its branch of the same name stays untouched).

Implements m1 of [specs/gcp.md](../gcp.md): one runner type, `linux`, end to end on
the applied `lolay-agents` foundation. Done when
[tests/sessions/linux.md](../../tests/sessions/linux.md) passes twice and the
orchestrator's memory on the `e2-micro` is measured.

Not in m1: `gis`, `android`, the GPU types, the cost notice (Billing Catalog lookup),
the dependency watch, locks, lock bot, nightly rebuild and manifests (m4).

## Decisions

Made while planning, from reading the code, the Terraform and the installed tools.
Each is written into the spec by s1 so the spec matches what gets built.

1. **Jobs are deployed through the Cloud Run v2 REST API, not `gcloud run jobs deploy`.**
   gcloud 570 has no flag for a disk-backed volume (`--add-volume` offers
   `cloud-storage`, `in-memory`, `nfs`); the v2 API has `emptyDir.medium: DISK` with a
   `sizeLimit`. `scripts/cloud/job-spec.sh` renders the Job JSON from the env files and
   the deploy `PATCH`es it with `allowMissing=true` (create or update, idempotent).
   The first deploy proves Cloud Run accepts `DISK` for jobs; if it wants a launch
   stage, the spec gets `launchStage: BETA`.
2. **No gcloud in the orchestrator image.** The hook and the supervisor both call REST
   with `curl` and a metadata-server token (Storage, Cloud Run, Secret Manager). The
   image is Claude Code, curl, jq and the scripts. The spec's "Contents: Claude Code,
   gcloud" changes.
3. **The marker uses the JSON API's `ifGenerationMatch=0` query parameter** (412 means
   the order already has an execution). The spec's `x-goog-if-generation-match` header
   is the XML API's.
4. **The work order reaches the runner as an execution env override, as the spec says,
   and the entrypoint moves it to `~/.claude-runner/environment-secret` (mode 600) and
   unsets it** before starting the runner, so sessions never see it in their
   environment. It's still readable on the execution resource; it's spent at
   registration.
5. **Jobs run with `maxRetries: 0`, one task.** A retried task would re-register with a
   spent work order.
6. **The entrypoint runs `agent-runner` without `EPHEMERAL`**: in a container the
   runner's exit ends the execution, so there's nothing to power off. It writes
   `runner.env` from the job's env first, so `agent-runner`, `claude.sh` and the
   watchdog run unchanged apart from two settings (s3). The watchdog runs as a 30 s
   loop in the background (no systemd).
7. **The image's user is `ubuntu`** (uid 1000, already in `ubuntu:24.04`), with
   passwordless sudo, the same user as the Linux image.
8. **Claude upgrades itself at start in m1** (`CLAUDE_UPGRADE=true`, the shared
   default). The spec's "no self-update" relies on the m4 watch rebuilding within the
   hour; without it, an image is only as fresh as the last push, and
   `tests/sessions/linux.md` fails its version check after the next release. m4 turns
   it off. The image still installs Claude in its own last layer.
9. **The SessionStart hook prints the time-limit line now and the cost line only when
   `AGENT_COST_USD_PER_HOUR` is set.** The cost figure is the deploy's Billing Catalog
   lookup, which is out of m1. s6 changes the test's notice check to mark the cost
   half "skipped (not deployed)" when the variable is unset, so the test can pass.
   The time zone (`AGENT_NOTICE_TZ`) moves from `project.env` (Terraform outputs only)
   to `linux.env`.
10. **The deploy diffs against the last successful push deploy, not
    `github.event.before`.** GitHub's concurrency queue keeps one pending run and
    cancels the rest, so a diff from `before` would drop the cancelled run's changes.
    The base is the head SHA of the last successful `push` run of the deploy workflow
    (`GITHUB_TOKEN`, `actions: read`; no GCP), falling back to everything. A failed run
    redeploys more next time, never less.
11. **No `make cloud-publish` or `cloud-promote`.** Only `main` deploys, and the WIF
    condition already refuses anything else; a local target would be a second path
    around it with Gary's Owner credentials. m1 ships `cloud-build`, `cloud-smoke`,
    `cloud-test`, `cloud-ci` and `cloud-status` (read-only). The spec's target list
    changes; `cloud-lock` stays for m4.
12. **Unit tests live in `tests/unit/`**, Python `unittest` driving the scripts through
    `subprocess` (the style of the removed `test_make_kcpassword.py`), with a small fake
    GCP HTTP server so `curl` runs for real. Outside `hooks/gcp/**` and `images/cloud/**`
    so a test change doesn't redeploy anything or land in an image.
13. **Outputs mount with `uid=1000,gid=1000,implicit-dirs`** so the unprivileged user can
    write and sees folders other sessions created. The datasets file cache on scratch
    waits for `gis` (m2), where it matters.
14. **Key rotation is a VM reset, not re-setting the same digest.** The VM's unit skips
    a value equal to the running image, so the spec's "set the metadata to the same
    digest" does nothing.

15. **No standby for `linux` in m1 (`MIN_IDLE=0`, Gary 2026-11-05).** The spec's
    `--min-idle 1` holds a 4 vCPU, 16 GiB execution all month, about $0.37/hour or
    $270/month at Cloud Run's instance rates. Sessions wait for a cold start, which m1
    measures; standby is a one-line change to `linux.env` later.

## Shape

Creates:
- `infra/gcp/project.env` — Terraform's outputs, committed (spec "Outputs")
- `infra/gcp/linux.env` — the `linux` type's resources and runner settings
- `.dockerignore` — the image builds use the repo root as context; keep it to the dirs they read
- `images/cloud/docker-bake.hcl` — one build definition for make and CI; `linux` gets `agent-base` as a bake target context
- `images/cloud/base/Dockerfile` — the spec's `agent-base`
- `images/cloud/base/profile.sh` — login-shell PATH and the scratch variables; the Linux image's `profile.sh` carries the Android SDK paths
- `images/cloud/base/claude/CLAUDE.md` — the cloud "This machine" text with Storage and time limit
- `images/cloud/base/claude/settings.json` — the SessionStart hook, merged with the shared settings at build (jq), so the Stop hook stays in one place
- `images/cloud/base/claude/hooks/session-start.sh` — per-session `AGENT_OUTPUTS` and the notice
- `images/cloud/entrypoint.sh` — the spec's entrypoint
- `images/cloud/linux/Dockerfile` — `FROM agent-base`, as is
- `images/cloud/orchestrator/Dockerfile` — the spec's `agent-orchestrator`
- `images/cloud/orchestrator/supervisor.sh` — one orchestrator per type, restart, signals, memory log
- `hooks/gcp/spawn-runner` — the spec's hook
- `hooks/gcp/lib.sh` — token and REST helpers shared by the hook and the supervisor
- `scripts/cloud/job-spec.sh` — renders the v2 Job JSON (decision 1)
- `scripts/cloud/changed-targets.sh` — the spec's path map, as a testable script
- `scripts/cloud/deploy.sh` — `type`, `orchestrator`, `prune`, `status`: what the workflow and `make cloud-status` run
- `.github/workflows/cloud-deploy.yml` — deploy on `main`
- `.github/workflows/cloud-build.yml` — build-only check on pull requests
- `tests/unit/fake_gcp.py` — fake metadata, Storage, Run and Secret Manager endpoints for the tests
- `tests/unit/test_entrypoint.py`
- `tests/unit/test_session_start.py`
- `tests/unit/test_spawn_runner.py`
- `tests/unit/test_supervisor.py`
- `tests/unit/test_job_spec.py`
- `tests/unit/test_changed_targets.py`
- `specs/handoffs/handoff-gcp-linux-heron.md` — session handoff with Gary's commands

Modifies:
- `specs/gcp.md`
- `specs/gcp-infrastructure.md`
- `tests/sessions/linux.md`
- `images/shared/runners/claude.sh`
- `images/shared/agent-runner-watchdog.sh`
- `images/linux/scripts/install-packages.sh`
- `Makefile`
- `Makefile.md`
- `README.md`

## Milestones

### m1 - `linux`

#### s1 - [exec] Bring the specs in line with what was built (done)

`specs/gcp-infrastructure.md` and `specs/gcp.md`, checked against
`lolay/infrastructure` `stacks/gcp/agents`:

- Cloud NAT exists: the orchestrator VM has no external IP and reaches Anthropic
  through it. Drop the "few dollars for its external IP" line and NAT from m2's list.
- Registry cleanup keeps 2 versions per image, still a dry run (both specs; the
  "One image per type" text becomes two, and rollback can still be a revert).
- `agent-orchestrator` has `storage.objectUser` on the orders bucket (the hook deletes
  its marker when a spawn fails), replacing `objectCreator` and `objectViewer`.
- `agent-images-ci`: custom role `orchestratorDeployer` (`compute.instances.get`,
  `setMetadata`) on the orchestrator VM only, `zoneOperationsReader` at project level,
  and `iam.serviceAccountUser` on `agent-orchestrator`.
- Scratch is 10 GiB per instance for every type for now, no quota approval; `gis`,
  `linux-gpu` and `3d-gpu` get more later; the region's 100 GiB is granted on first
  use. The type table's scratch column says so.
- `cloudbilling.googleapis.com` is enabled for the later cost notice, not used in m1.
- The orchestrator VM's contract as built: COS, a systemd unit long-polling
  `orchestrator-image`, digest-only under the registry, `--restart=always`,
  `--stop-timeout 85`, stdout to Cloud Logging, no SSH, and the exact
  `gcloud compute instances add-metadata` the deploy runs.
- Status lines: infrastructure applied 2026-11-05; gcp.md "m1 in progress".
- Decisions 1 to 15 above where they change the spec (job deploy through REST, no
  gcloud on the host, `ifGenerationMatch`, the work-order file, `maxRetries: 0`, the
  deploy base, the make targets, Claude upgrade until m4, notice time zone, outputs
  mount options, rotation by reset, no standby).

Done: both specs read true against the Terraform; nothing else changes.

#### s2 - [fast] Commit the GCP env files (done)

- `infra/gcp/project.env`: exactly the fifteen values Gary gave, nothing added.
- `infra/gcp/linux.env`: `CPU=4`, `MEMORY=16Gi`, `SCRATCH_GIB=10`,
  `TASK_TIMEOUT_SEC=34200` (9 h 30 min: the 480 min session cap plus an hour of unused
  standby plus margin), `RETIRE_MARGIN_SEC=900`, `MIN_IDLE=0`,
  `EXPECTED_SPAWN_SECONDS=180`, `EXIT_IF_UNUSED_MIN=60`,
  `RELEASE_IDLE_SESSION_MIN=60`, `KILL_SESSION_AFTER_MIN=480`,
  `AGENT_NOTICE_TZ=America/Los_Angeles`. Comment each, as `vms/example-linux.env`
  does.

Done: both files source cleanly in bash.

#### s3 - [exec] Two settings in the shared runner files (done)

- `images/shared/runners/claude.sh`: `CLAUDE_RETIRE_AT` (epoch seconds, passed as
  `--retire-at`) and `CLAUDE_EXIT_IF_UNUSED_MIN` (`--exit-if-unused-min`), both off
  when empty, documented in the header like the others. The Mac and Linux images
  leave them unset, so nothing changes there.
- `images/shared/agent-runner-watchdog.sh`: read sessions from
  `${CLAUDE_BASE_DIR:-$HOME/workspace}/_sessions`, with `CLAUDE_BASE_DIR` read from
  `runner.env` the way `WATCHDOG_ACTION` is.
- `images/linux/scripts/install-packages.sh`: `INSTALL_CLAUDE` (default `true`); the
  cloud build sets `false` and installs Claude in its own last layer.

Done: `shellcheck -x` and `shfmt -d` clean; `--help` of the installed Claude still
lists both flags.

#### s4 - [exec] The base and `linux` images (done)

- `.dockerignore`: everything out except `images/shared`, `images/linux/packages.txt`,
  `images/linux/Brewfile`, `images/linux/scripts`, `images/cloud`, `hooks/gcp`,
  `infra/gcp`.
- `images/cloud/docker-bake.hcl`: targets `base`, `linux`, `orchestrator`; `linux`
  takes `agent-base` from `target:base`; variables `REGISTRY`, `TAG`,
  `CLAUDE_VERSION` (default `latest`).
- `images/cloud/base/Dockerfile`, in the spec's layer order: `ubuntu:24.04` → sudo and
  a sudoers entry for `ubuntu` → `install-packages.sh` as `ubuntu` with
  `INSTALL_CLAUDE=false` (apt, Homebrew, Brewfile) → `images/shared` files into
  `~/bin` and `~/.claude` (settings merged with jq), `profile.sh`, the cloud
  `CLAUDE.md` and hooks, the entrypoint → Claude Code with
  `install.sh $CLAUDE_VERSION` last. `USER ubuntu`, `ENTRYPOINT agent-cloud-entrypoint`.
- `images/cloud/base/profile.sh`: PATH (`~/bin`, `~/.local/bin`, Homebrew) and the
  scratch variables from the entrypoint, so login shells get them too.
- `images/cloud/linux/Dockerfile`: `FROM agent-base`.

Done: `docker buildx bake -f images/cloud/docker-bake.hcl linux --load` builds here;
`docker run --rm --entrypoint bash agent-linux -lc 'claude --version; brew --version; sudo -n true'`
passes.

#### s5 - [exec] The entrypoint, with a smoke mode (done)

`images/cloud/entrypoint.sh` (installed as `agent-cloud-entrypoint`):

- `/scratch` must be a mount (fail loudly if it's missing, since the rest of the
  filesystem is memory); create `/scratch/{workspace,tmp,cache}` owned by `ubuntu`.
- Export `AGENT_SCRATCH=/scratch`, `AGENT_DATASETS=/mnt/datasets`,
  `TMPDIR=/scratch/tmp`, `XDG_CACHE_HOME=/scratch/cache`,
  `PIP_CACHE_DIR=/scratch/cache/pip`, `HOMEBREW_CACHE=/scratch/cache/Homebrew`,
  `CLAUDE_BASE_DIR=/scratch/workspace`.
- `AGENT_RETIRE_AT` = start + `AGENT_TASK_TIMEOUT_SEC` − `AGENT_RETIRE_MARGIN_SEC`,
  exported and passed as `CLAUDE_RETIRE_AT`.
- Move `SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET` to the secret file (decision 4), write
  `runner.env` (`AGENT=claude`, label, base dir, idle and session limits,
  exit-if-unused), start the watchdog loop, `exec agent-runner`.
- `smoke` as the first argument: check Claude runs, `/scratch` is a real disk (not
  tmpfs, writable), `/mnt/outputs` round-trips a small file, `/mnt/datasets` refuses a
  write, `sudo -n true`, `brew --version`; print a pass or fail line per check and exit
  non-zero on any fail. The deploy runs it on `agent-<type>-next`.
- Every path above overridable for tests (`AGENT_SCRATCH_ROOT` and the like), and the
  runner command (`AGENT_RUNNER_COMMAND`), defaulting to the real ones.

`tests/unit/test_entrypoint.py`: env and paths, retire-at arithmetic, secret file mode
600 and the variable gone from the runner's env, `runner.env` contents, missing
scratch fails, smoke reports each check.

Done: unit tests pass; `docker run --rm -v <tmpdir>:/scratch agent-linux smoke`
passes the checks that don't need the buckets (outputs and datasets mounted as local
dirs, datasets read-only).

#### s6 - [exec] Session storage: CLAUDE.md and the SessionStart hook

- `images/cloud/base/claude/CLAUDE.md`: "This machine" for Cloud Run (disposable
  container, `ubuntu` with sudo, no Android, no display), the spec's Storage and
  time-limit sections (cost sentence only when the cost is known).
- `images/cloud/base/claude/hooks/session-start.sh`: session id from
  `CLAUDE_CODE_REMOTE_SESSION_ID`, else `CLAUDE_CODE_SESSION_ID`, else the payload's
  `session_id`; `mkdir -p /mnt/outputs/<type>/<UTC date>/<id>`; append
  `export AGENT_OUTPUTS=...` to `$CLAUDE_ENV_FILE`; emit `systemMessage` and
  `additionalContext` with the facts line (scratch free, `gs://` outputs path, top of
  datasets) and the time line: idle limit from `CLAUDE_RELEASE_IDLE_SESSION_MIN`, end
  = min(now + `CLAUDE_KILL_SESSION_AFTER_MIN`, `AGENT_RETIRE_AT`) in
  `AGENT_NOTICE_TZ` with the time remaining; cost line only when set. Never fails the
  session: any error prints a warning line and exits 0.
- `images/cloud/base/claude/settings.json`: the `SessionStart` entry.
- `tests/sessions/linux.md`: the notice check marks the cost half skipped when
  `AGENT_COST_USD_PER_HOUR` is unset (decision 9). Also records which id variable the
  hook used, the spec's open question.

`tests/unit/test_session_start.py`: id precedence, folder and env-file line, end time
picks the earlier deadline, time zone formatting, JSON shape, no cost line without
the variable, outputs not mounted still exits 0.

Done: unit tests pass; in the built image, the hook run by hand with a sample payload
prints valid JSON.

#### s7 - [exec] `hooks/gcp/spawn-runner`

- `hooks/gcp/lib.sh`: load `project.env` and the type's env; `gcp_token` from the
  metadata server; `gcp_call METHOD URL [body on stdin]` with the token passed as a
  header file (`-H @file`) so it's never in argv, `--max-time`, and the HTTP status
  returned separately from the body. Base URLs overridable for tests.
- The hook, the spec's four steps: unset the environment secret; create
  `orders/$CLAUDE_RUNNER_ORDER_ID` with `ifGenerationMatch=0` (412: exit 0); build the
  `:run` body with `jq --rawfile` from `CLAUDE_RUNNER_WORK_ORDER_FILE` and send it on
  stdin, with `SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET` and
  `RUNNER_LABEL=<type>-<order id prefix>` as container env overrides; any failure
  after the marker deletes it. Exit 2 for 403 and 404 (setup broken: no job, no
  permission), 1 for everything else. stderr carries the status and the API's error
  message, never a body that was sent.
- `tests/unit/fake_gcp.py` and `tests/unit/test_spawn_runner.py`: marker then run,
  request shape (path, overrides, label), redelivery (412) runs nothing, run 500
  deletes the marker and exits 1, run 404 exits 2, token failure exits 1, the work
  order never appears on stderr or in any request but the run body.

Done: unit tests pass; `shellcheck -x` clean.

#### s8 - [deep] The orchestrator container and supervisor

- `images/cloud/orchestrator/Dockerfile`: small Debian or Ubuntu base, curl, jq,
  ca-certificates, Claude Code native install, a non-root user; `hooks/gcp`, the
  supervisor and `infra/gcp/*.env` baked in.
- `images/cloud/orchestrator/supervisor.sh`:
  - types = `infra/gcp/*.env` except `project.env`;
  - per type, read `claude-env-<type>` (`versions/latest:access`) into
    `/dev/shm/agent-orchestrator/<type>` mode 600; a type whose secret can't be read is
    logged and retried, the others still start;
  - start `claude self-hosted-runner orchestrator --hooks-dir … --environment-secret-file
    <its own file> --min-idle --expected-spawn-seconds --health-port 8080+i
    --shutdown-drain-sec 70 --hook-timeout 60` with `RUNNER_TYPE` and the env files in
    its environment;
  - prefix each line with its type without losing the child's pid (the log pipe must
    not be what gets signalled or waited on);
  - restart an exited orchestrator with backoff;
  - on SIGTERM, forward to every orchestrator at once and wait for all, inside
    docker's 85 s;
  - every `MEMORY_LOG_INTERVAL_SEC` (300): one line per type,
    `supervisor: memory type=<t> pid=<p> pss_kib=… rss_kib=…` from
    `/proc/<pid>/smaps_rollup` (orchestrator plus descendants), a total line with
    `MemAvailable`, and each `/healthz` `connected` state.
- `tests/unit/test_supervisor.py` with a fake `claude` and the fake Secret Manager:
  type discovery, ports, secret files and modes, each orchestrator gets only its own
  secret, prefixed logs, restart after exit, SIGTERM reaches every child and the
  supervisor exits after them, memory lines parse, a missing secret doesn't block the
  other types.

Why `[deep]`: process supervision in bash, where signal forwarding through
log-prefix pipes, restart backoff and the 70/85 s drain budget are easy to get
subtly wrong, and a mistake loses spawns during every deploy.

Done: unit tests pass; the image builds and `docker run` with a fake secret source
starts and stops cleanly within the budget.

#### s9 - [exec] Job spec, deploy script and the path map

- `scripts/cloud/job-spec.sh <type> <image>`: the v2 Job JSON: `agent-runner` service
  account, gen2, `maxRetries: 0`, one task, `timeout`, CPU and memory, env
  (`AGENT_TYPE`, task timeout, margin, limits, `AGENT_NOTICE_TZ`), volumes `scratch`
  (`emptyDir` `DISK`, `SCRATCH_GIB`), `outputs` (GCS, decision 13), `datasets` (GCS
  read-only), mounts, label `managed-by=agent-images` and `agent-images-sha`.
- `scripts/cloud/changed-targets.sh <base> <head>`: the spec's map to a JSON list of
  `{target, build}`; `infra/gcp/<type>.env` alone means `build: false` (reuse the live
  digest); `project.env` and the shared paths mean everything; types come from the env
  files, and the `FROM` chain from the Dockerfiles.
- `scripts/cloud/deploy.sh`:
  - `type <t>`: build and push `<image>:<date>-<sha>` (or read the live digest),
    `PATCH` `agent-<t>-next` with `allowMissing=true`, `gcloud run jobs execute
    agent-<t>-next --args=smoke --wait`, then `PATCH` `agent-<t>`;
  - `orchestrator`: build, push, `gcloud compute instances add-metadata
    agent-orchestrator --zone us-central1-a --metadata orchestrator-image=<digest ref>`;
  - `prune`: delete `agent-*` jobs labelled `managed-by=agent-images` with no env file;
  - `status`: each job's image and last execution, the orchestrator's metadata digest.
- `tests/unit/test_job_spec.py`, `tests/unit/test_changed_targets.py` (a temporary git
  repo with commits touching each row of the map).

Done: unit tests pass; `job-spec.sh linux <digest>` output checked against the v2
schema's field names (the discovery document).

#### s10 - [exec] Workflows

- `.github/workflows/cloud-deploy.yml`: `push` to `main` and `workflow_dispatch`
  (targets, or `all`). Job `plan` (no `environment`, `contents: read`,
  `actions: read`, no `id-token`) runs `changed-targets.sh` from the last successful
  push run's SHA (decision 10). Jobs `types` (matrix, `max-parallel: 1`) and
  `orchestrator` (after `types`, so the job exists before the first standby spawn)
  run in `environment: production` with `id-token: write`, authenticate through
  `WIF_PROVIDER` as `CI_SERVICE_ACCOUNT` from `project.env`, `prune` last. Workflow
  `concurrency` queues, never cancels. GitHub Actions cache for layers.
- `.github/workflows/cloud-build.yml`: pull requests touching the cloud paths; no
  `id-token`, no environment; `make cloud-ci` (shellcheck, shfmt, unit tests, bake
  build of all three images, no push).
- Action versions looked up from their releases at the time (checkout stays at the
  repo's `v7.0.1`), pinned to release tags like the existing workflows.

Done: both files pass `actionlint` (if it installs here) or a YAML parse plus a
read-through; no job without `environment: production` has `id-token: write`.

#### s11 - [exec] Make targets and docs

- `Makefile`: `##@ Cloud` with `cloud-build` (bake, `--load`, `TYPE=` optional),
  `cloud-smoke` (run `smoke` locally against temp dirs), `cloud-test` (the unit tests),
  `cloud-ci` (lint of the cloud scripts, `cloud-test`, `cloud-build`), `cloud-status`
  (read-only, Gary's gcloud). New scripts join `SHELL_SCRIPTS`.
- `Makefile.md`: a Cloud section. `README.md`: GCP moves from "designed" to "m1
  building", with a pointer to the spec and `make cloud-status`.

Done: `make help` lists them; `make cloud-ci` passes here.

#### s12 - [exec] Local verification and handoff

Everything that runs without GCP, in one pass on the final tree: `shellcheck -x` and
`shfmt -d` on every script, `make cloud-test`, `make cloud-build`, `make cloud-smoke`,
the orchestrator container start and stop, `job-spec.sh` against the discovery
schema. Refresh the handoff with results and the first-deploy checklist.

Done: all green, recorded in the handoff with what could not be tested here (Cloud Run
accepting the `DISK` volume, FUSE mounts, WIF, the real orchestrator).

#### s13 - Owner: production environment, merge, first deploy

Gary creates the `production` environment with a main-only branch policy, reviews the
PR (plan shown on it), merges; the deploy runs. Commands in the handoff.

#### s14 - Owner: the claude.ai environment and two test runs

Gary checks "Agent: Linux" exists in claude.ai (and, if he recreates it, adds the new
key and resets the VM), runs `tests/sessions/linux.md` twice, and reads the
orchestrator's memory from Cloud Logging. Commands in the handoff.

#### s15 - [exec] Record m1's results

From Gary's two reports and the memory lines: findings fixed (each its own commit),
the measured Pss per orchestrator and the `e2-micro` verdict into `specs/gcp.md`,
which notice channel claude.ai shows (drop the redundant one, as the spec asks), the
session id variable the runner sets, the cold start into `EXPECTED_SPAWN_SECONDS`,
m1 marked done, and the milestone handoff for m2.

Done: the spec reads as built; the plan and session handoff removed or promoted
before the PR merges.
