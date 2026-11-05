# Handoff: m1 `linux` on GCP

Branch: `claude/eloquent-mayer-ivkml8` in `lolay/agent-images` (assigned by the
harness). `lolay/infrastructure` has a branch of the same name with nothing on it; m1
needs no Terraform change.

Plan: [plan-gcp-linux-heron.md](plan-gcp-linux-heron.md). s1 to s12 are done and
pushed, run unattended as Gary approved (`MIN_IDLE=0`). Stopped at s13, which is
Gary's.

## Done

One commit per step, `m1.s1` to `m1.s12`, on top of `09f92e7`.

- s1: both specs match the applied Terraform and the plan's decisions.
- s2: `infra/gcp/project.env` (the 15 values, nothing added) and `infra/gcp/linux.env`.
- s3: `claude.sh` passes `--retire-at` and `--exit-if-unused-min`. The watchdog reads
  `CLAUDE_BASE_DIR`. `install-packages.sh` can skip Claude.
- s4: `agent-base` and `agent-linux`, built with buildx bake (2.87 GB).
  `build-essential` joins `packages.txt`, because Homebrew refuses the `lolay/tap`
  formulae without a system compiler. The LXD image would have hit this too.
- s5: the entrypoint, with `smoke` mode.
- s6: the cloud `CLAUDE.md` and the SessionStart hook. The session test now skips
  the cost line until the cost notice is deployed.
- s7: `hooks/gcp/spawn-runner` and `hooks/gcp/lib.sh`.
- s8: the orchestrator image (630 MB) and its supervisor.
- s9: `job-spec.sh`, `changed-targets.sh` and `deploy.sh`.
- s10: `cloud-deploy.yml` and `cloud-build.yml`. Pinned to google-github-actions/auth
  v3.0.0, setup-gcloud v3.0.1, docker/setup-buildx-action v4.4.1 and bake-action
  v7.5.0, the newest tags on 2026-11-05.
- s11: the `make cloud-*` targets, `Makefile.md` and the README.
- s12: final verification (below), plus two fixes from it: the supervisor also
  collects any orchestrator that's no longer running on every pass, and the tests'
  teardown can't deadlock.

## Verified here

All of this ran in this container with no GCP access:

- `shellcheck -x` and `shfmt -d` pass on every script in `make lint`, the cloud
  scripts included.
- actionlint passes. A script checked that only the `production` jobs ask for
  `id-token`.
- 66 unit tests (`make cloud-test`) passed 20 full runs in a row. They drive the real
  scripts against fakes: `tests/unit/fake_gcp.py` (metadata, Storage, Run v2,
  Secret Manager), a fake `claude` and a fake `gcloud`.
- `agent-linux` builds and `make cloud-smoke` passes every check.
- The runner's normal path in Docker: it starts with every flag
  (`--exit-if-unused-min 60`, `--retire-at`), reaches Anthropic and gets the expected
  401 on a fake work order.
- The SessionStart hook in the image prints valid JSON with a PST end time. The
  merged `settings.json` has both the SessionStart and Stop hooks.
- The orchestrator image against the fake GCP server: it reads the secret over REST,
  starts a real `claude` orchestrator with `hooks/gcp` (which rejects the fake key
  and is restarted with backoff), logs memory lines, and on `docker stop` exits 0.
- The job body's field names and enums match the Cloud Run v2 discovery schema.

Local builds go through a scratchpad-only bake override for this sandbox's TLS proxy
(HTTPS apt and the proxy's CA). Nothing in the repo depends on it.

## Not testable here

The first deploy shows these:

1. **Scratch as `emptyDir` `medium: DISK` on a job.** The schema allows it, and the
   field's description still says "only MEMORY or none". If the API rejects it, try
   `LAUNCH_STAGE=BETA` in `linux.env`: `job-spec.sh` passes it through as the job's
   `launchStage`. If that fails too, scratch has no managed path on Cloud Run jobs,
   which is a design question for Gary.
2. **The GCS FUSE mount options** (`uid=1000`, `gid=1000`, `implicit-dirs`). Smoke
   checks that outputs round-trip a file and that datasets refuses a write.
3. **WIF, the registry push, `PATCH allowMissing`, the smoke execution and
   `add-metadata`** with the real roles.
4. **The orchestrator on COS:** the metadata token from inside the container, Secret
   Manager access through the IAM condition, and logs reaching Cloud Logging.
5. **Cold start** of a 2.9 GB image plus `claude update` at start, against
   `EXPECTED_SPAWN_SECONDS=180`.

## Gotchas

- Create `production` before merging. GitHub creates an environment a workflow names
  if it doesn't exist, with no branch policy. The WIF condition still pins
  `refs/heads/main`, but the branch policy is one of the three locks.
- The first push deploys everything: there's no earlier successful deploy to diff
  from.
- The layer cache keeps apt and Homebrew at whatever they were when each layer was
  first built, until their inputs change. Dispatch the workflow with `no-cache` to
  refresh them; m4's nightly build does it on a schedule. Claude Code is the
  exception: its version is resolved every run.
- The orchestrator's Claude Code only updates when the orchestrator image rebuilds
  (a change to `hooks/gcp`, `images/cloud/orchestrator` or `infra/gcp`, or a dispatch
  naming `orchestrator`). The runners update themselves at start.
- For m2: a type's own tools come after Claude Code (`FROM agent-base`), so a Claude
  release rebuilds every type's tool layers. The spec's layer order puts type tools
  first. Fix it when `gis` adds real tools.

## Pending

PR #13 merged as `f2bd141`. Its first deploy failed at the smoke run on
`agent-linux-next`: every check passed except `sudo`, which Cloud Run's
no-new-privileges flag blocks. The CI identity can't read logs, so Gary pulled the
log by hand.

On this branch (restarted from `main`, with the old tip merged in `-s ours`, not
force-pushed), not in PRs yet:
- `c25541e`: the deploy prints a failed smoke execution's log. Needs
  `roles/logging.viewer` for `agent-images-ci`: `lolay/infrastructure` `16109d4` on
  its branch of the same name, `make ci` passing.
- `8fa4c0f`: no sudo on Cloud Run. Sessions stay `ubuntu` and install with
  Homebrew; smoke checks that Homebrew's prefix is writable; the outputs mount drops
  its uid/gid options (Cloud Run gave them uid 2000). Verified under Docker
  `--security-opt no-new-privileges`.

Open question for Gary, verbatim: "The mac runs as root with no issue. so why not
linux? Don't start until i give a go ahead." The alternative is running the linux
image as root (apt works; Homebrew through a `runuser` wrapper; Claude Code as root
untested). Don't start it, or open either PR, until Gary says go.

## Steps that need Gary

Commands are fish.

### s13: production environment, before merging

Create the environment with custom branch policies:

```fish
echo '{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}' | gh api -X PUT repos/lolay/agent-images/environments/production --input -
```

Allow `main` only:

```fish
gh api -X POST repos/lolay/agent-images/environments/production/deployment-branch-policies -f name=main -f type=branch
```

Check it:

```fish
gh api repos/lolay/agent-images/environments/production/deployment-branch-policies --jq '.branch_policies[] | .name + " " + .type'
```

Merge the PR, then watch the deploy:

```fish
gh run watch (gh run list --repo lolay/agent-images --workflow cloud-deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId') --repo lolay/agent-images
```

```fish
make cloud-status
```

The orchestrator's first minutes on the VM:

```fish
gcloud logging read 'resource.type="gce_instance" AND "supervisor:"' --project lolay-agents --freshness 30m --limit 50 --format 'value(timestamp,textPayload,jsonPayload.message)'
```

If the type job fails on the scratch volume, add `LAUNCH_STAGE=BETA` to
`infra/gcp/linux.env` in a PR (see "Not testable here", item 1).

### s14: the claude.ai environment and two test runs

In claude.ai, check that "Agent: Linux" is in the environment list. If it isn't and
you create it, its key replaces the one in Secret Manager:

```fish
read -s -P 'claude-env-linux key: ' key; and printf '%s' $key | gcloud secrets versions add claude-env-linux --project lolay-agents --data-file=-; set -e key
```

Then restart the orchestrator so it reads the new version (setting the same digest
again does nothing):

```fish
gcloud compute instances reset agent-orchestrator --zone us-central1-a --project lolay-agents
```

Run the test twice, each in a new session in "Agent: Linux" on `lolay/agent-images`,
sending `Run the runner test in tests/sessions/linux.md. Follow it exactly.`. Start
the second after the first ends, so its isolation check gets a fresh runner.

Read the orchestrator's memory (one line per type every 5 minutes, plus a total):

```fish
gcloud logging read 'resource.type="gce_instance" AND "supervisor: memory"' --project lolay-agents --freshness 1d --limit 40 --format 'value(timestamp,textPayload,jsonPayload.message)'
```

Send back both session branches (or their `report.md`) and a few memory lines from
during a session. s15 records them.

## How to resume

Read the plan, then start at the first step without `(done)`: s13 is Gary's, s15 is
next for an agent. After the PR merges, s15 goes on a new branch from `main`. Before
building locally: start `dockerd` if `docker info` fails, and install shellcheck,
shfmt and actionlint into a venv (`shellcheck-py`, `shfmt-py`, `actionlint-py`).
