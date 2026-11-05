# GCP runners (proposal)

Status: **proposed, not built.** Written 2026-11-04. Recheck anything marked preview
before building on it.

The same model as the Mac and Linux runners, with Google running the hardware: Claude's
orchestrator calls a hook per queued session, the hook starts one throwaway workload,
the workload registers with the single-use work order, serves one session, and is
gone. What changes is what the hook submits to (a Cloud Run job execution instead of a
Tart or LXD clone) and that the images are containers we build and publish ourselves.

Why GCP for lolay: Cloud Run jobs are the most managed way anywhere to get an
ephemeral session on a Blackwell GPU (RTX PRO 6000), with Google owning the driver,
scale to zero, and root allowed in the container. Azure has no managed container
service with Blackwell, so it would mean a VM per session and our own driver.

## Shape

```
claude.ai session queue
        │
        ▼
Compute Engine e2-micro  (free tier, always on, no GPU)
  one claude self-hosted-runner orchestrator per runner type, --hooks-dir hooks/gcp
        │ spawn-runner: gcloud run jobs execute <type> --update-env-vars ...
        ▼
Cloud Run job execution  (one per session)
  agent-cloud-entrypoint → Xvfb (`-gpu` types) → images/shared/runners/claude.sh
  registers with the work order, serves one session, exits → execution ends
```

| Today (Linux host) | GCP |
| --- | --- |
| `lxc launch agent-linux runner-N --ephemeral` | `gcloud run jobs execute agent-<type>`: one execution, gone when it exits |
| `build-image.sh` → `lxc publish agent-linux-next`, move the alias | Build and push to Artifact Registry, smoke-test `agent-<type>-next`, then point the job at the new digest |
| systemd user unit runs the orchestrator | One `e2-micro` running the orchestrator container |
| `~/.config/agent-images` holds the environment secret | Secret Manager, read only by the orchestrator's service account |
| `runner-once.sh` deletes the VM | Nothing to delete: the execution ends when the container exits |
| Claim `runner-1..N` with `mkdir` | Idempotency marker in GCS, created with `--if-generation-match=0` |

## Runner types

A runner type is one Claude environment. The environment is what you pick when
starting a session, so it's the selector: "Linux GPU", "GIS", "Mac", "Android" each show
up in the session picker by name, and a session runs on whatever backs that
environment. Types never share capacity, images or secrets.

Each type is four things, declared in `infra/gcp/<type>.env`:

| Part | Per type |
| --- | --- |
| Claude environment and its secret | Created in claude.ai; the secret goes in Secret Manager as `claude-env-<type>` |
| Image | `agent-<type>`, built `FROM` `agent-base` or `agent-base-gpu` plus the type's tools |
| Backend | A Cloud Run job (`agent-<type>`) with its resources, or a Compute Engine template |
| Orchestrator settings | `--min-idle`, `--expected-spawn-seconds`, runner flags |

| Type | Image | Backend and resources | Standby |
| --- | --- | --- | --- |
| `linux` | `agent-linux` (`agent-base` as is) | Cloud Run job, 4 vCPU, 16 GiB | 1 (cheap, instant starts) |
| `linux-gpu` | `agent-linux-gpu` (`agent-base-gpu` as is) | Cloud Run job, RTX PRO 6000, 20 vCPU, 80 GiB (that GPU's minimum); L4 for cheap tests | 0: a standby GPU bills the whole time |
| `gis` | `agent-gis` (from `agent-base`: GDAL, QGIS, PDAL) | Cloud Run job, CPU | 0 or 1 |
| `3d-gpu` | `agent-3d-gpu` (from `agent-base-gpu`: COLMAP built for Blackwell, Blender) | Cloud Run job, RTX PRO 6000 | 0 |
| `android` | Compute Engine image | Compute Engine VM per session with nested virtualization (the fallback below); Cloud Run has no KVM | 0 |
| `mac` | Not on GCP | agent-images' Macs (Tart), as today: Mac development, iOS included | Per host |

Mobile splits by platform: iOS needs macOS, which no GCP service runs, so it's part of
`mac`, on the Macs; Android needs KVM, which Cloud Run doesn't give, so it's a VM. Both still
appear in the same session picker, because the picker lists environments, not clouds.

**Naming.** A type is named for what the session is for, plus `-gpu` when it has
one: `linux`, `linux-gpu`, `gis`, `3d-gpu`, `android`, `mac`. CPU is the default,
so it isn't spelled out, and the suffix sits last so names sort by purpose and a GPU
variant lands next to its CPU sibling (`gis`, later `gis-gpu`). The suffix matters in
the picker, because a `-gpu` session costs an order of magnitude more. Other variants
compose the same way if they come (`linux-arm64`). Every resource takes the type's
name: environment "Agent: 3D (GPU)" in claude.ai, `infra/gcp/3d-gpu.env`, secret
`claude-env-3d-gpu`, image and job `agent-3d-gpu`, `images/cloud/3d-gpu/`. The two
build parents, `agent-base` and `agent-base-gpu`, aren't types and never deploy on
their own. (`agent-linux` here is a Cloud Run image; the Linux host's LXD alias of
the same name is the Android image, and would be clearer renamed `agent-android`.)

**One host for all orchestrators, one orchestrator per type.** An orchestrator
registers with one environment's secret, so it serves exactly one type; there's no
single orchestrator for all of GCP. But each is a small stateless polling process, so
one always-on instance runs them all: a supervisor starts an orchestrator per
`infra/gcp/*.env` file, each with its own secret, hooks directory and `RUNNER_TYPE`.
Adding a type is a new env file, a new image directory and a new environment in
claude.ai; the push to `main` deploys it.

**Standbys can be zero; orchestrators can't.** `--min-idle 0` (the default) keeps no
runner waiting: a runner starts only when a session queues, and the session waits for
the cold start (image pull and boot, minutes for a large GPU image). That's the
setting for every `-gpu` type, and for any type where a wait is fine. The
orchestrators themselves must stay up: they poll Anthropic for work, and nothing
calls in, so with none running, sessions sit in the queue.

**The orchestrator host: start with the smallest.** One Compute Engine `e2-micro` in
`us-central1` runs the orchestrator container (below) for every type.

| Host | Memory | Monthly cost, always on |
| --- | --- | --- |
| `e2-micro` (start here) | 1 GB | $0 in GCP's free tier (one per billing account, `us-central1`, `us-west1` or `us-east1`), plus possibly a few dollars for its external IP; check the billing report |
| `e2-small` (if it runs out of memory) | 2 GB | About $12 |
| Cloud Run worker pool, 1 vCPU, 512 MiB | | Roughly $45 to $50 at instance-based rates (unverified; Google's page didn't load) |

An orchestrator measured about 40 MB at startup on 2026-11-05 (it exited before
connecting, without a real key, so steady state is still to measure). Six of them,
plus the OS and Docker, should fit in 1 GB; m3 checks, and the move to `e2-small` is
one setting. Polling is small, and image pulls from Artifact Registry in the same
region are free. The orchestrators are the only always-on cost: every runner is a
Cloud Run execution billed only while its session runs.

The host runs Container-Optimized OS with no inbound access at all. A systemd unit
starts the container and long-polls the metadata server
(`?wait_for_change=true`) for an `orchestrator-image` key. A deploy sets that key to
the new digest, and the unit pulls it and restarts the container, so deploys need no
SSH.

**Choosing by repo instead (not proposed).** The hook gets
`CLAUDE_RUNNER_PRIMARY_REPO_URL`, so one environment could pick the image from the
repo (GIS repos to `agent-gis`). Standbys have no repo, though, so they'd all have to
be one image, and you'd lose the explicit choice. Separate environments are simpler
to reason about.

Region: `us-central1`, the one with both L4 and RTX PRO 6000 on Cloud Run.

## Images

We manage them, in this repo, next to the macOS and Linux images, and they share
`images/shared` so sessions behave the same everywhere.

```
images/cloud/
  base/Dockerfile      ubuntu:24.04; images/linux/packages.txt; Homebrew + Brewfile
                       as the runner user; Claude Code native installer;
                       images/shared (runner, watchdog, Stop hook, ~/.claude seed)
  base-gpu/Dockerfile  ARG BASE (CUDA 13.0 runtime on ubuntu:24.04), then the base
                       layers; Xvfb, VirtualGL, mesa-utils, vulkan-tools; optional noVNC
  <type>/Dockerfile    FROM agent-base or agent-base-gpu, adds the type's tools
  entrypoint.sh        start Xvfb when DISPLAY is wanted, then exec agent-runner
```

Choices, carried over from [design.md](design.md):

- **Track latest, pin on breakage.** No versions in package lists. The pipeline below
  rebuilds whenever anything upstream moves, so images stay current without a
  self-update on every start.
- **One unprivileged user with passwordless sudo**, like `admin` and `ubuntu`, not
  root: sessions can install what a repo needs, and nothing outlives the execution.
- **CUDA matches the host driver.** Cloud Run mounts its driver (580.x, CUDA 13.0) at
  `/usr/local/nvidia/lib64`, so `agent-base-gpu` uses a CUDA 13.0 userland. Newer CUDA
  needs NVIDIA's forward-compatibility package. Anything compiled (COLMAP, PyTorch
  extensions) must target compute capability 12.0 or carry PTX, or it fails on
  Blackwell.
- **No secrets in images or sessions** ([secrets.md](secrets.md)). The runner's
  service account gets logging only.

Layer order, slowest-moving first, so a rebuild only redoes what changed:

```
FROM <base>@<digest>      ubuntu:24.04, or nvidia/cuda 13.0 runtime for gpu
apt packages              packages.txt, at the locked snapshot
Homebrew + Brewfile       incl. lolay/tap
type tools                gis, 3d, ...
images/shared files
Claude Code               ARG CLAUDE_VERSION; RUN install that exact version
manifest                  /etc/agent-images/manifest.json, also an OCI label
```

Claude goes last because it moves most often (most weekdays): a new release rebuilds
one layer.

## Image pipeline

Everything runs in GitHub Actions on hosted runners, free for a public repo. Images go
one way: built on GitHub, pushed into Artifact Registry (ingress, free), never pulled
back out.

**Only `main` deploys.** Every change to what runs in Cloud Run is a commit on `main`:
a lock update from the watch, a change to `images/cloud` or `images/shared`, or a
revert. A push to `main` runs the deploy workflow; nothing else pushes images or
updates jobs. Enforced in three places, so one mistake doesn't open it up:

- The deploy workflow triggers on `push` to `main` and `workflow_dispatch`, and its
  jobs run in a GitHub environment, `production`, whose deployment branch policy
  allows only `main`.
- The workload identity provider's attribute condition accepts only this repo's
  numeric id with `ref` `refs/heads/main` and that environment, so a token from any
  other branch, pull request or fork can't get GCP credentials at all.
- Pull requests that touch the images run a build-only check: build, no push, no GCP
  credentials.

### What a push to `main` deploys

Any push to `main` that changes what a GCP runtime runs redeploys that runtime, and
only that one. The workflow's first job diffs the push (`github.event.before` to
`github.sha`) against this map and hands the affected targets to the deploy job as a
matrix:

| Path changed | Redeploys |
| --- | --- |
| `images/shared/**`, `images/linux/packages.txt`, `images/linux/Brewfile`, `images/cloud/base/**`, `images/cloud/entrypoint.sh` | Every runner type |
| `images/cloud/base-gpu/**` | Every `-gpu` type (each is built `FROM` it) |
| `images/cloud/<type>/**`, including its `versions.lock` | That type |
| `hooks/gcp/**`, `images/cloud/orchestrator/**` | The orchestrator host (new image, then the host restarts its container) |
| `infra/gcp/<type>.env` (CPU, memory, GPU, timeout, `--min-idle`) | That type's job and orchestrator settings, no image build |
| Anything else (`specs/`, the macOS and Linux images, README) | Nothing |

The `FROM` chain comes from the Dockerfiles themselves, so adding a type doesn't mean
editing the map. When the diff can't be computed (a first push, or `before` is all
zeros after a force push), it deploys everything. A manual dispatch takes a list of
targets, or `all`.

### The lock

Each image type has a lock in git, `images/cloud/<type>/versions.lock`: every
dependency resolved to an exact version. It's the record of what the live image was
built from, and the check compares upstream against it, so nothing ever reads the
image.

| Dependency | Locked as | Resolved by (on the hosted runner, from upstream only) |
| --- | --- | --- |
| Base image | Digest | `crane digest ubuntu:24.04` (or the CUDA tag): a registry HEAD, no pull |
| apt packages, dependencies included | An Ubuntu snapshot timestamp, plus the full resolved list | `apt-get install --simulate` of `packages.txt` in a stock `ubuntu:24.04` container at the locked base digest, once with `--snapshot <locked>` and once against today's archive. Any difference in the resolved set means apt moved |
| Homebrew, dependencies and `lolay/tap` included | Each formula and cask with its version and revision | `brew info --json=v2` on the runner's own Homebrew (preinstalled on Ubuntu runners), after `brew tap lolay/tap` |
| Claude Code | Version | `https://downloads.claude.ai/claude-code-releases/latest` |
| Type tools (COLMAP, ...) | Version | The project's latest GitHub release |

The build consumes the lock: `FROM` the locked digest, `apt-get --snapshot` at the
locked timestamp, `CLAUDE_VERSION` from the lock. apt's snapshot makes the apt layer
reproducible, old versions included, which plain `pkg=version` can't promise because
Ubuntu's update pockets keep only the newest. Homebrew is the exception: it only
installs current versions, so its lock entries are a record and a change detector,
not a pin.

The watch never touches GCP, so there's no egress. The stock Ubuntu image it starts
is about 30 MB, from a public registry.

### Watch: hourly, 08:00 to 22:00 Pacific

`cron: '17 8-22 * * *'` with `timezone: America/Los_Angeles` (schedules take a time
zone since March 2026). It never deploys; it only proposes a commit:

1. Resolve everything as above into a candidate lock, per type.
2. Same as the lock on `main`: done, a minute or two of runner time.
3. Different: commit the candidate lock to `main` with a message naming what moved
   (`lock(3d-gpu): claude 2.1.318, apt 3 packages`). That push starts the deploy.

The lock on `main` is the desired state, and the deploy makes Cloud Run match it. If
a deploy fails, `main` is ahead of what's live until it's fixed: the failed run
notifies, the watch doesn't re-propose a lock that's already on `main`, and the
nightly build retries.

The commit needs a GitHub App token (`actions/create-github-app-token`), not
`GITHUB_TOKEN`: a push made with `GITHUB_TOKEN` doesn't start other workflows, so the
deploy would never run. Direct commit, not a pull request: deploys follow upstream
within the hour, and the lock bot below is what keeps that safe.

### The lock bot

A GitHub App's permissions are per repo and per kind (contents, pull requests, ...),
not per path, and the rule that restricts file paths on push (push rulesets) is
Enterprise Cloud only, for private and internal repos. So "it can only change
`versions.lock`" is built from layers:

| Layer | What it stops |
| --- | --- |
| Installed on `lolay/agent-images` only; permissions Contents write and Metadata read, nothing else | Any other repo; issues, pull requests, settings, secrets |
| No Workflows permission | GitHub refuses any change from the App to `.github/workflows/`, so it can't rewrite the workflows that guard it |
| Each run's token narrowed further (`permission-contents: write` on `actions/create-github-app-token`), valid one hour | A leaked token outliving its run |
| The App's private key is a secret of a GitHub environment, `lock-bot`, that only `main` can use | Any branch, pull request or fork using the key; only reviewed code on `main` runs with it |
| `main` ruleset: pull requests required, squash only, the App as the only bypass actor, no force pushes or deletions | Anyone else committing directly; the App rewriting history |
| The watch checks its own diff before pushing: only `images/cloud/*/versions.lock`, fast-forward only | A bug in the resolver writing anything else |
| An audit workflow on every push to `main` by the App: any path outside the lock files fails the run and notifies | Anything that slips past the above, caught within minutes |

Commits made through the API (`createCommitOnBranch`) are signed by GitHub, so a
"require signed commits" rule works with the bot too.

Strongest option if that's not enough: put the locks in a separate repo
(`lolay/agent-images-locks`) with the App installed only there, and have it notify
this repo (`repository_dispatch`) to deploy. The App then can't touch this repo at
all. More moving parts; not worth it for a lock file.

Expect one or two deploys a day: Claude most weekdays, apt security updates a few
times a week, Homebrew less.

### Deploy: build, smoke, promote

Runs only for a push to `main` (or a manual dispatch on `main`), one type at a time
in a `concurrency` group that queues rather than cancels.

Layer caching through the Actions cache (`cache-to type=gha`), not the registry, so a
build reads nothing back from GCP. The cache holds 10 GB per repo, so a CUDA image may
miss and rebuild in full; that costs time, not money. Hosted runners have about 14 GB
of free disk, so GPU builds clear the runner's preinstalled toolchains first, the
usual step for large images.

Every deploy follows the `-next` pattern from `build-image.sh`:

1. Push `us-central1-docker.pkg.dev/<project>/agent-images/<image>:<date>-<sha>`,
   where `<sha>` is the `main` commit being deployed.
2. Point `agent-<type>-next` at that digest and run one smoke execution in Cloud Run:
   `claude --version`, `nvidia-smi`, a CUDA device probe, `eglinfo` against Xvfb. It
   runs in GCP, next to the registry, so the pull is free.
3. Only if it passes, point `agent-<type>` at the same digest. Cloud Run resolves the
   image when the job is updated, so running sessions never see a half-built image.

### Nightly: 04:00 Pacific, Tuesday through Saturday

A scheduled run of the deploy workflow on `main` (scheduled workflows always run on
the default branch): a no-cache build with `--pull` of the lock on `main`, promoted
the same way. It catches what the lock can't see (a curl installer), retries a failed
deploy, and keeps the "track latest" promise even if a check is wrong.

### Standbys

Only `linux` keeps one (`--min-idle 1`), and it runs with `--exit-if-unused-min 60`,
so a standby is never more than an hour older than the newest image. `-gpu` types keep
none. No recycling job needed.

### Keep one image, keep the history

- **One image per type.** An Artifact Registry cleanup policy keeps the most recent
  version and deletes the rest, untagged layers included. Rollback is a `git revert`
  of the lock on `main`, which deploys like any other commit: apt and the base come
  back exactly (snapshot and digest), Homebrew comes back current. A revert alone
  lasts only until the next watch re-proposes the newer version, so pair it with a pin
  on whatever broke (below).
- **History in git, logs on GitHub.** The lock's history is the permanent record of
  what every image contained, and `git log -p images/cloud/3d-gpu/versions.lock` shows
  what changed and when. Build logs, with the full `dpkg-query -W` and
  `brew list --versions` output, keep 90 days in Actions (a public repo's maximum) and
  are public, which is fine: builds hold no secrets.

Make targets in the same vocabulary as the rest: `make cloud-lock TYPE=3d-gpu` (resolve
locally, show the diff), `make cloud-build`, `make cloud-publish`, `make cloud-promote`,
`make cloud-status`.

Off the shelf instead: Renovate (as a GitHub Action) handles the base digest, GitHub
releases and a custom source for Claude, with a pull request per change. It doesn't
resolve an apt dependency closure or Homebrew on Linux, and a pull request per bump
is a lot of churn for "track latest". One small resolver script and one lock commit
fit better. Dependabot still suits the pinned GitHub Actions versions.

## Pinning a dependency

Same rule as [design.md](design.md#track-the-latest-pin-only-on-breakage): pin only the
thing that broke, with a comment naming the upstream issue, and remove the pin once
it's fixed. How, per source:

| Dependency | Pin | Notes |
| --- | --- | --- |
| Base image | `FROM ubuntu:24.04@sha256:...` | The watch reports it as pinned instead of rebuilding |
| apt package | `name=version` in `packages.txt`, resolved against the locked snapshot | The snapshot keeps old versions installable. To hold all of apt still, freeze the snapshot timestamp in the lock |
| Homebrew formula | A versioned formula if one exists (`python@3.12`), else `brew extract --version=X <formula> lolay/tap` and name `lolay/tap/<formula>@X` in the Brewfile | `brew pin` doesn't help a fresh build. An extracted formula has no bottle, so it builds from source |
| `lolay/tap` formula | Revert in the tap, or add a versioned formula there | It's ours |
| Claude Code | `CLAUDE_VERSION=2.1.317` (a version) or `stable` (the channel) | The watch then skips Claude; set the runner's `CLAUDE_UPGRADE=false` so it doesn't update itself past the pin |
| Type tool from a release | Its version in the Dockerfile | |

Pins live where the dependency is declared, each with `# pinned: <issue URL>`, and the
lock marks them pinned. The watch lists every pin in the job summary next to
upstream's current version and the issue's state, so a pin whose issue closed stands
out instead of rotting.

## The hook

`hooks/gcp/spawn-runner`, the counterpart of `hooks/linux/spawn-runner`, same contract
(0 submitted, 1 retry, 2 don't retry; nothing secret on stderr):

1. `unset SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET`.
2. Create `gs://<bucket>/orders/$CLAUDE_RUNNER_ORDER_ID` with
   `--if-generation-match=0`. If it already exists, this order has an execution:
   exit 0. A lifecycle rule deletes markers after a day.
3. `gcloud run jobs execute "agent-$RUNNER_TYPE" --region us-central1 --async
   --update-env-vars SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET=<work order>,RUNNER_LABEL=<type>-<order id prefix>`.
   The work order travels as an execution override. It's visible to anyone who can
   read the execution, but it's single-use and spent at registration, so a session
   that reads it gets nothing, the same reasoning as design.md. If that's not enough,
   write it to a GCS object only the runner's service account can read and pass the
   path instead.
4. Any failure after the marker deletes the marker and exits 1.

Orchestrator flags: `--expected-spawn-seconds` set from measured cold starts (multi-GB
GPU images are minutes, not seconds), `--shutdown-drain-sec 70` with an 85 s stop
budget. Runner flags, already in `claude.sh`: `--capacity 1`,
`--release-idle-session-min`, `--kill-session-after-min` below the job's task timeout,
plus `--retire-at` set at start to the task deadline minus a margin, so Cloud Run's
hard stop releases sessions cleanly instead of losing them. GPU jobs run up to
12 hours (preview since 2026-10-30).

## The orchestrator container

One container image, `agent-orchestrator` (`images/cloud/orchestrator/`), runs every
type's orchestrator. Claude's orchestrator takes one `--environment-secret-file`, so
it's one process per type inside one container:

- **Contents:** Claude Code, `gcloud`, `hooks/gcp`, and a supervisor script as the
  entrypoint. Small, CPU only, and it never runs session code.
- **Types:** the supervisor reads every `infra/gcp/*.env` baked into the image and
  starts `claude self-hosted-runner orchestrator` for each, with `RUNNER_TYPE`, that
  type's `--min-idle` and `--expected-spawn-seconds`, its own `--health-port`
  (8080 upward), and `--shutdown-drain-sec 70`.
- **Secrets:** each type's `claude-env-<type>`, read from Secret Manager at start by
  the host's service account and written to a file per type (mode 600, tmpfs). The
  supervisor passes each orchestrator only its own, and the hook unsets it before
  doing anything else.
- **Supervision:** restart any orchestrator that exits; forward `SIGTERM` to all of
  them; prefix each one's log lines with its type. Healthy only when every
  orchestrator's `/healthz` reports `connected`.
- **Deploys:** a push to `main` that touches `hooks/gcp/**`, `images/cloud/orchestrator/**`
  or `infra/gcp/*.env` rebuilds it and restarts it on the host. Sessions in flight are
  unaffected: they belong to runners, not to the orchestrator.

One process for all types would mean one environment for all types, with the hook
picking an image per session. That loses the explicit choice in the picker, and
standbys, which start before any session exists, couldn't know which image to be.

## Setup

The GCP project is `lolay-agents`, set up by Terraform in `lolay/infrastructure`
(foundation: project, registry, bucket, network, the orchestrator VM, service
accounts, IAM, workload identity federation, GPU quota). This repo owns the
workloads: images, the Cloud Run jobs, the orchestrator container, and the runner
types, so adding a type never needs a Terraform change. Each type's Claude
environment key goes into Secret Manager by hand as `claude-env-<type>`; the
orchestrator's access is granted by that name prefix. What Terraform needs, in full:
[handoffs/handoff-gcp-infrastructure-lantern.md](handoffs/handoff-gcp-infrastructure-lantern.md).

Terraform's outputs (project, registry path, federation provider, service accounts,
bucket, VM) are committed here as a copy in one env file for the workflows, per the
CI configuration rule: non-secret, reviewed, and Terraform stays the source.

The GitHub side is set by hand: the lock-bot App, the `production` and `lock-bot`
environments, and the `main` ruleset (see [The lock bot](#the-lock-bot)).

## The head

A virtual head: Xvfb, OpenGL through VirtualGL/EGL on the GPU, screenshots for
computer use, noVNC if you want to watch. The open question is whether Cloud Run's
mounted driver includes the graphics libraries (`libEGL_nvidia`, `libGLX_nvidia`,
Vulkan ICD) or only compute. The docs only describe the compute path. That's the first
spike, because it decides between Cloud Run and the fallback below. Software rendering
(llvmpipe) on 20 vCPUs works for screenshots in the meantime, slowly.

## Platforms

What each GCP service can run, for runner types beyond Linux x86:

| Platform | Cloud Run | GKE | Compute Engine |
| --- | --- | --- | --- |
| Linux x86_64 | Yes | Yes | Yes |
| Linux Arm64 | No: Cloud Run expects amd64 images | Yes (Axion C4A, Tau T2A nodes) | Yes (C4A, T2A) |
| Windows | No | Windows Server node pools | Windows Server images |
| macOS | No | No | No |
| Android emulator | No (no `/dev/kvm`) | Nested virtualization node pools | Yes, with nested virtualization: Intel series and AMD N4D; not E2 or Arm |
| NVIDIA GPU | L4, RTX PRO 6000 | L4, RTX PRO 6000, A100, H100, Blackwell B200/GB200 | Same as GKE |

macOS stays on Macs. The one cloud option is AWS: EC2 Mac instances are bare-metal
Mac minis (M2, M2 Pro, M4, M4 Pro) on Dedicated Hosts with a 24-hour minimum. Tart
runs on them (Cirrus publishes Tart AMIs for EC2 Mac), so an EC2 Mac can be an
agent-images run host unchanged: the host stays up, and the per-session VMs live
inside it, which makes the 24-hour minimum irrelevant. Same two-VM license limit,
and guests can't be newer than the host, so it needs AWS's macOS 27 AMI. One open
Tart issue reports VMs failing to start on a headless `mac2.metal`.

## Fallback: a Compute Engine VM per session

If Cloud Run can't render, or a type needs Docker in the session, a full GPU desktop,
or nested virtualization:

- **Image:** Packer's `googlecompute` builder, provisioned by the same scripts as the
  LXD image, plus NVIDIA's driver on `-gpu` types. A Compute Engine image instead of a
  container; same `-next` promotion.
- **Hook:** `gcloud compute instances create` from an instance template (G4 for
  RTX PRO 6000, fractional sizes available; N2/C3 for CPU), the work order in instance
  metadata that the guest reads once and deletes, `--max-run-duration` with
  `--instance-termination-action=DELETE` as the backstop, and the guest deletes its
  own VM when the runner exits.
- **Warm starts:** a managed instance group standby pool of stopped or suspended VMs.
- **Android:** Compute Engine supports nested virtualization on Intel VMs (and AMD
  N4D), and with a VM per session the emulator is one level deep, not the two that
  ruled cloud VMs out in [linux.md](linux.md). Worth a test before buying the NUC.

That's more to own (driver, OS image), which is why it's the fallback, not the start.

## Milestones

### m1 - CPU runner by hand

`agent-linux` image, a Cloud Run job, an environment secret, and `claude
self-hosted-runner` started by a manual execution with the real secret. Proves the
image, the Stop hook and the git proxy in Cloud Run.

### m2 - GPU spike

`agent-linux-gpu` on an L4 first (cheaper), then RTX PRO 6000: `nvidia-smi`, CUDA probe,
`eglinfo` and `vulkaninfo` against Xvfb, a Blender EEVEE render, a computer-use
screenshot of a GL app. Measure cold start. Decides Cloud Run or the fallback.

### m3 - Orchestrator and hook

`hooks/gcp/spawn-runner`, the orchestrator image and its host, order markers. A
session from claude.ai lands on a fresh execution and the execution ends with it.

### m4 - Image pipeline

GitHub Actions build, smoke and promote; the hourly watch and nightly rebuild;
cleanup policy; manifest artifacts; `make cloud-*` targets; README version table rows.

### m5 - Fallback, only if m2 says so

The Compute Engine path above.

## Open items

| Item | Why |
| --- | --- |
| Graphics libraries on Cloud Run GPUs | Decides whether a virtual head runs on Cloud Run |
| Cold start of a multi-GB GPU image | Sets `--expected-spawn-seconds` and whether a standby is worth paying for |
| Cloud Run GPU quota grant | New projects get a default grant; may need a request for RTX PRO 6000 |
| Data | Agerpoint's data is in Azure; for lolay, keep data-heavy work next to wherever its data is, since cross-cloud egress is about $0.08 to $0.12 per GB |

## References

- [Claude Code on-demand runners](https://code.claude.com/docs/en/self-hosted-environments-configuration)
- [GCP network egress pricing](https://cloud.google.com/vpc/network-pricing)
- [EC2 Mac instances](https://aws.amazon.com/ec2/instance-types/mac/), [Tart on AWS Marketplace](https://tart.run/blog/2023/10/06/tart-is-now-available-on-aws-marketplace/), [Tart issue on headless mac2.metal](https://github.com/cirruslabs/tart/issues/1146)
- [Cloud Run GPU jobs](https://docs.cloud.google.com/run/docs/configuring/jobs/gpu), [execute jobs with overrides](https://docs.cloud.google.com/run/docs/execute/jobs), [worker pools](https://docs.cloud.google.com/run/docs/configuring/workerpools/gpu), [release notes](https://docs.cloud.google.com/run/docs/release-notes)
- [MIG standby pools](https://docs.cloud.google.com/compute/docs/instance-groups/suspended-and-stopped-vms-in-mig)
