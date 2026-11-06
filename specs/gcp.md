# GCP runners

Status: **m1 (`linux`) done, 2026-11-05**: sessions run on Cloud Run, tested twice
([m1 results](#m1-results)). Written 2026-11-04; the foundation is applied
(`lolay/infrastructure`, `stacks/gcp/agents`). m2 is under way
([handoffs/handoff-m2-gis-android.md](handoffs/handoff-m2-gis-android.md)): `gis`
landed in lolay/agent-images#18, and `android` (a Compute Engine VM per session) is
built and waiting for its first deploy. Recheck
anything marked preview before building on it.

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
        │ spawn-runner: POST jobs/agent-<type>:run (curl), env overrides
        ▼
Cloud Run job execution  (one per session)
  agent-cloud-entrypoint → Xvfb (`-gpu` types) → images/shared/runners/claude.sh
  registers with the work order, serves one session, exits → execution ends
```

| Today (Linux host) | GCP |
| --- | --- |
| `lxc launch agent-android runner-N --ephemeral` | A run of job `agent-<type>` through the Cloud Run Admin API: one execution, gone when it exits |
| `build-image.sh` → `lxc publish agent-android-next`, move the alias | Build and push to Artifact Registry, smoke-test `agent-<type>-next`, then point the job at the new digest |
| systemd user unit runs the orchestrator | One `e2-micro` running the orchestrator container |
| `~/.config/agent-images` holds the environment secret | Secret Manager, read only by the orchestrator's service account |
| `runner-once.sh` deletes the VM | Nothing to delete: the execution ends when the container exits |
| Claim `runner-1..N` with `mkdir` | Idempotency marker in GCS, created with `ifGenerationMatch=0` |

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

| Type | Image | Backend and resources | Scratch disk | Standby |
| --- | --- | --- | --- | --- |
| `linux` | `agent-linux` (`agent-base` as is) | Cloud Run job, 4 vCPU, 16 GiB | 10 GiB | 0: a standby holds 4 vCPU and 16 GiB, about $270 a month; 1 later if cold starts hurt |
| `linux-gpu` | `agent-linux-gpu` (`agent-base-gpu` as is) | Cloud Run job, RTX PRO 6000, 20 vCPU, 80 GiB (that GPU's minimum); L4 for cheap tests | 10 GiB now, 100 GiB later | 0: a standby GPU bills the whole time |
| `gis` | `agent-gis` (toolsets core, native, node, gis: GDAL, PDAL, QGIS from conda-forge) | Cloud Run job, 8 vCPU, 32 GiB (Cloud Run's most memory; gis is bound by memory, not CPU) | 10 GiB now, 100 GiB later | 0: a standby is about $550 a month |
| `3d-gpu` | `agent-3d-gpu` (from `agent-base-gpu`: COLMAP built for Blackwell, Blender) | Cloud Run job, RTX PRO 6000 | 10 GiB now, 200 GiB (two volumes) later | 0 |
| `android` | Compute Engine image | Compute Engine VM per session with nested virtualization ([below](#compute-engine-vm-per-session-android-and-the-gpu-fallback)); Cloud Run has no KVM | 100 GB boot disk | 0 |
| `mac` | Not on GCP | agent-images' Macs (Tart), as today: Mac development, iOS included | The VM's disk | Per host |

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
their own. (The Linux host's LXD image, the Android one, was also called
`agent-linux` until [image-toolsets.md](image-toolsets.md) s5; it's `agent-android`.)

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
| `e2-micro` (start here) | 1 GB | $0 in GCP's free tier (one per billing account, `us-central1`, `us-west1` or `us-east1`). No external IP: egress goes through Cloud NAT, about a dollar a month plus $0.045 per GB through it |
| `e2-small` (from the GPU milestone, or sooner if memory runs out) | 2 GB | About $12 |
| Cloud Run worker pool, 1 vCPU, 512 MiB | | Roughly $45 to $50 at instance-based rates (unverified; Google's page didn't load) |

Memory, measured: a long-running orchestrator on a Mac (2026-11-05) had a resident size
of 126 MB; one on Linux was about 40 MB at startup before connecting. Resident size
counts the Claude binary's shared code once per process, so several orchestrators
share part of it; on Linux, `Pss` in `/proc/<pid>/smaps_rollup` gives the real share.
Measured on the `e2-micro` in m1 (2026-11-05, during a session): the `linux`
orchestrator's Pss was 78 to 84 MiB, the supervisor's under 2 MiB, and the VM had
about 610 of its 967 MiB available. So `gis` and `android` (m2) at about 85 MiB each
leave around 440 MiB; with the GPU types too it would be about 270 MiB, which is why
the host moves to `e2-small` at m3. The hook adds to that if it shells out to
`gcloud` (Python, around 100 MB for each run), so the hook calls the Cloud Run and
Storage REST APIs with `curl` and a token from the metadata server instead: small,
and a second faster per spawn. m1 measures it; moving to `e2-small` is one setting.

The host runs Container-Optimized OS with no inbound access and no external IP (it
reaches Anthropic through Cloud NAT). Its contract, as built in `lolay/infrastructure`:

- A systemd unit long-polls instance metadata key `orchestrator-image`
  (`?wait_for_change=true`). When the value changes, it pulls that image and runs it
  as container `agent-orchestrator` with `--restart=always` and `--stop-timeout 85`;
  a failed pull keeps the running container.
- It accepts only a digest under the registry,
  `us-central1-docker.pkg.dev/lolay-agents/agent-images/<name>@sha256:...`; anything
  else is logged and ignored. A value equal to the running image does nothing, so
  restarting the same image means resetting the VM.
- The deploy sets the key with
  `gcloud compute instances add-metadata agent-orchestrator --zone us-central1-a --metadata orchestrator-image=<digest ref>`,
  so deploys need no SSH, and there is none.
- The container's stdout goes to Cloud Logging. The container gets no environment
  from the host: everything it needs is baked into the image.

**Choosing by repo instead (not proposed).** The hook gets
`CLAUDE_RUNNER_PRIMARY_REPO_URL`, so one environment could pick the image from the
repo (GIS repos to `agent-gis`). Standbys have no repo, though, so they'd all have to
be one image, and you'd lose the explicit choice. Separate environments are simpler
to reason about.

Region: `us-central1`, the one with both L4 and RTX PRO 6000 on Cloud Run.

## Images

Each type's tools are a list of toolsets ([image-toolsets.md](image-toolsets.md)),
so no type inherits another's. `agent-base-gpu` (m3) is still to come, on CUDA's devel
image rather than runtime.

We manage them, in this repo, next to the macOS and Linux images, and they share
`images/shared` so sessions behave the same everywhere.

```
images/cloud/
  base/Dockerfile      ubuntu:24.04 through mirror.gcr.io, the runner user, and the
                       core toolset; a build parent only
  base-gpu/Dockerfile  (m3) the same on CUDA 13.0 devel; Xvfb, VirtualGL, mesa-utils,
                       vulkan-tools; optional noVNC
  <type>/Dockerfile    FROM agent-base or agent-base-gpu, adds the type's toolsets,
                       one layer each (bake target <type>-tools)
  runner/Dockerfile    FROM the type's tools: images/shared (runner, watchdog, Stop
                       hook, ~/.claude seed), the session's CLAUDE.md from its
                       toolsets, profile.sh, then Claude Code last (target <type>)
  entrypoint.sh        set AGENT_SCRATCH and AGENT_DATASETS, point CLAUDE_BASE_DIR at
                       /scratch/workspace, TMPDIR at /scratch/tmp and the caches
                       (XDG_CACHE_HOME, pip, Homebrew) at /scratch/cache, move the
                       work order to a file, start Xvfb when DISPLAY is wanted, then
                       exec agent-runner (AGENT_OUTPUTS comes per session, below)
```

Choices, carried over from [design.md](design.md):

- **Track latest, pin on breakage.** No versions in package lists. The pipeline below
  rebuilds whenever anything upstream moves, so images stay current without a
  self-update on every start.
- **One unprivileged user, and no root on Cloud Run.** The images run as `ubuntu`,
  like the Linux image. Cloud Run sets the kernel's no-new-privileges flag on every
  container, so `sudo` (and with it `apt-get install`) can't work there: found by m1's
  first smoke run, 2026-11-05. Sessions install with Homebrew, which the user owns;
  root instead would lose that, since Homebrew refuses to run as root. So the base
  image carries the usual toolchains already (the Linux image's toolsets: Node,
  Python, Go, Rust, protobuf, Terraform), rather than leave every
  session to install them. The `android` VMs keep passwordless sudo.
- **CUDA matches the host driver.** Cloud Run mounts its driver (580.x, CUDA 13.0) at
  `/usr/local/nvidia/lib64`, so `agent-base-gpu` uses a CUDA 13.0 userland. Newer CUDA
  needs NVIDIA's forward-compatibility package. Anything compiled (COLMAP, PyTorch
  extensions) must target compute capability 12.0 or carry PTX, or it fails on
  Blackwell.
- **Docker Hub images come through Google's mirror** (`mirror.gcr.io/library/ubuntu`,
  `mirror.gcr.io/nvidia/cuda`): Docker Hub rate-limits anonymous pulls, which CI and
  local builds hit. The mirror is a pull-through cache of Docker Hub, so a digest is
  the same on both; a tag can trail Docker Hub's for a while after it moves.
- **No secrets in images or sessions** ([secrets.md](secrets.md)). The runner's
  service account gets logging and the two buckets (below) only.
- **Claude upgrades itself at start until m4.** The plan is no self-update: the watch
  rebuilds within the hour of a release. Until the watch exists, an image is only as
  fresh as the last push, so the runner keeps `CLAUDE_UPGRADE=true` (the shared
  default) and m4 turns it off. The image still installs Claude in its own last layer.

Layer order, slowest-moving first, so a rebuild only redoes what changed:

```
FROM <base>@<digest>      ubuntu:24.04, or nvidia/cuda 13.0 runtime for gpu,
                          both through mirror.gcr.io
core toolset              apt, Homebrew, the shared session tools (agent-base)
type toolsets             one layer each: apt at the locked snapshot, Brewfile,
                          install.sh (images/cloud/<type>)
images/shared files       and the session files (images/cloud/runner)
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
only that one. The workflow's first job diffs `github.sha` against the head of the
last successful push deploy (not `github.event.before`: GitHub's concurrency queue
keeps one pending run and cancels the others, so their pushes would never be
deployed) against this map, and hands the affected targets to the deploy jobs as a
matrix. A failed deploy makes the next one redeploy more, never less:

| Path changed | Redeploys |
| --- | --- |
| `images/shared/**`, `images/toolsets/install-toolset.sh`, `images/toolsets/write-claude-md.sh`, `images/cloud/base/**`, `images/cloud/runner/**`, `images/cloud/entrypoint.sh`, `images/cloud/docker-bake.hcl`, `.dockerignore` | Every runner type |
| `images/toolsets/<toolset>/**` | The types whose Dockerfiles (or build parents') install that toolset |
| `images/cloud/base-gpu/**` | Every `-gpu` type (each is built `FROM` it) |
| `images/cloud/<type>/**`, including its `versions.lock` | That type |
| `hooks/gcp/**`, `images/cloud/orchestrator/**` | The orchestrator host (new image, then the host restarts its container) |
| `infra/gcp/<type>.env` (CPU, memory, GPU, timeout, `--min-idle`) | That type's job and orchestrator settings, no image build |
| `scripts/cloud/job-spec.sh` | Every type's job, no image build |
| `images/android/**` (the LXD image's scripts and files, and `gce/`), the toolsets in `images/android/toolsets.txt`, `images/shared/**`, the two toolset scripts | The `android` Compute Engine image: Packer build, smoke VM, then family `agent-android` ([below](#compute-engine-vm-per-session-android-and-the-gpu-fallback)) |
| `infra/gcp/android.env` | The orchestrator only: the hook reads it at each spawn, from the orchestrator image |
| Anything else (`specs/`, the macOS image, README) | Nothing |

The `FROM` chain comes from the Dockerfiles themselves, so adding a type doesn't mean
editing the map. `infra/gcp/project.env` changes everything. When the diff can't be
computed (no earlier successful deploy, or its commit is gone after a force push), it
deploys everything. A manual dispatch takes a list of targets, or `all`.

### The lock

Each image type has a lock in git, `images/cloud/<type>/versions.lock`: every
dependency resolved to an exact version. It's the record of what the live image was
built from, and the check compares upstream against it, so nothing ever reads the
image.

| Dependency | Locked as | Resolved by (on the hosted runner, from upstream only) |
| --- | --- | --- |
| Base image | Digest | `crane digest mirror.gcr.io/library/ubuntu:24.04` (or the CUDA tag): a registry HEAD, no pull |
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
   where `<sha>` is the `main` commit being deployed. A change to the type's env file
   alone skips the build and reuses the live digest.
2. Point `agent-<type>-next` at that digest and run one smoke execution in Cloud Run
   (the entrypoint's `smoke` mode): `claude --version`, scratch is a real writable
   disk, outputs round-trips a file, datasets refuses a write, Homebrew's prefix is
   writable by the session user; for
   `-gpu` types also `nvidia-smi`, a CUDA device probe, `eglinfo` against Xvfb. It
   runs in GCP, next to the registry, so the pull is free.
3. Only if it passes, point `agent-<type>` at the same digest. Cloud Run resolves the
   image when the job is updated, so running sessions never see a half-built image.

Jobs are written through the Cloud Run v2 REST API (`PATCH` with `allowMissing=true`,
so the same call creates or updates), from a Job spec rendered from the env files by
`scripts/cloud/job-spec.sh`. `gcloud run jobs deploy` can't: its `--add-volume` has no
disk-backed type, while the API's `emptyDir` takes `medium: DISK`. Every job runs
with `maxRetries: 0` and one task: a retried task would come back with a spent work
order.

### Nightly: 04:00 Pacific, Tuesday through Saturday

A scheduled run of the deploy workflow on `main` (scheduled workflows always run on
the default branch): a no-cache build with `--pull` of the lock on `main`, promoted
the same way. It catches what the lock can't see (a curl installer), retries a failed
deploy, and keeps the "track latest" promise even if a check is wrong.

### Standbys

None in m1. A standby is a whole execution held while it waits: for `linux` that's
4 vCPU and 16 GiB, about $0.37 an hour or $270 a month. If cold starts hurt, `linux`
gets one (`MIN_IDLE=1`), and it runs with `--exit-if-unused-min 60` (set for every
type already), so a standby is never more than an hour older than the newest image.
`-gpu` types keep none. No recycling job needed.

### Keep one image, keep the history

- **Two images per type.** An Artifact Registry cleanup policy keeps the two most
  recent versions and deletes the rest, untagged layers included (still a dry run).
  Two, because after a push whose smoke test fails, the live digest is no longer the
  newest. Rollback is a `git revert`
  of the lock on `main`, which deploys like any other commit: apt and the base come
  back exactly (snapshot and digest), Homebrew comes back current. A revert alone
  lasts only until the next watch re-proposes the newer version, so pair it with a pin
  on whatever broke (below).
- **History in git, logs on GitHub.** The lock's history is the permanent record of
  what every image contained, and `git log -p images/cloud/3d-gpu/versions.lock` shows
  what changed and when. Build logs, with the full `dpkg-query -W` and
  `brew list --versions` output, keep 90 days in Actions (a public repo's maximum) and
  are public, which is fine: builds hold no secrets.

Make targets in the same vocabulary as the rest: `make cloud-build` (build the
images locally), `make cloud-smoke` (run the entrypoint's smoke mode against local
directories), `make cloud-test` (the unit tests), `make cloud-ci` (what the pull
request check runs), `make cloud-status` (read-only: what each job and the host run),
and from m4 `make cloud-lock TYPE=3d-gpu` (resolve locally, show the diff). No
publish or promote target: only `main` deploys, and a local one would be a second
path around the federation condition.

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
| Base image | `FROM mirror.gcr.io/library/ubuntu:24.04@sha256:...` | The watch reports it as pinned instead of rebuilding |
| apt package | `name=version` in `packages.txt`, resolved against the locked snapshot | The snapshot keeps old versions installable. To hold all of apt still, freeze the snapshot timestamp in the lock |
| Homebrew formula | A versioned formula if one exists (`python@3.12`), else `brew extract --version=X <formula> lolay/tap` and name `lolay/tap/<formula>@X` in the Brewfile | `brew pin` doesn't help a fresh build. An extracted formula has no bottle, so it builds from source |
| `lolay/tap` formula | Revert in the tap, or add a versioned formula there | It's ours |
| Claude Code | `CLAUDE_VERSION=2.1.317` (a version) or `stable` (the channel) | The watch then skips Claude; set the runner's `CLAUDE_UPGRADE=false` so it doesn't update itself past the pin |
| Type tool from a release | Its version in the Dockerfile | |

Pins live where the dependency is declared, each with `# pinned: <issue URL>`, and the
lock marks them pinned. The watch lists every pin in the job summary next to
upstream's current version and the issue's state, so a pin whose issue closed stands
out instead of rotting.

## Keys

Each runner type's Claude environment key reaches only the orchestrator. A runner
gets a single-use work order instead, and a session gets nothing.

| Step | Who | What |
| --- | --- | --- |
| 1 | You, once per type | Create the environment in claude.ai, copy its key, `gcloud secrets create claude-env-<type>` and `versions add` it. It never touches git, Terraform state or an image |
| 2 | The orchestrator host, at container start | Its service account (`agent-orchestrator`, allowed only `claude-env-*`) reads each type's secret from Secret Manager (REST, metadata-server token) and writes it to a file per type in `/dev/shm`, mode 600 |
| 3 | Each type's orchestrator | Starts with `--environment-secret-file` pointing at its own file only, and polls Anthropic with it |
| 4 | Anthropic, per queued session | Sends that orchestrator a spawn request carrying a work order: a signed, single-use token for one runner |
| 5 | The hook | Unsets the environment key from its own environment, then starts a Cloud Run execution with the work order as `SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET`, or an `android` VM with it as instance metadata key `work-order` |
| 6 | The entrypoint, or the VM's `agent-gce-order` at boot | Moves the work order to `~/.claude-runner/environment-secret` (mode 600) and unsets the variable, so sessions don't inherit it. On a VM, the guest marks the order read (guest attribute `agent-images/order`) and the reaper removes the metadata key within a minute |
| 7 | The runner | Registers with the work order, which is spent at that moment, then claims the session |

So a session that reads its environment or the execution's metadata finds a spent
token. The runner's service account (`agent-runner`) has no Secret Manager access at
all. Rotating a key is a new secret version in claude.ai and Secret Manager, then a
reset of the host (`gcloud compute instances reset agent-orchestrator`), so the
container starts again and reads it. Setting `orchestrator-image` to the digest
already running does nothing.

## The hook

`hooks/gcp/spawn-runner`, the counterpart of `hooks/linux/spawn-runner`, same contract
(0 submitted, 1 retry, 2 don't retry; nothing secret on stderr):

1. `unset SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET`.
2. Create `gs://<bucket>/orders/$CLAUDE_RUNNER_ORDER_ID` with `ifGenerationMatch=0`
   (the Storage JSON API through `curl` with a metadata-server token, like every call
   the hook makes; no `gcloud` on the host). A 412 means this order already has an
   execution: exit 0. A lifecycle rule deletes markers after a day.
3. Run the job with overrides: `POST .../jobs/agent-$RUNNER_TYPE:run` on the Cloud
   Run Admin API, with `SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET=<work order>` and
   `RUNNER_LABEL=<type>-<order id prefix>` as container env overrides (what
   `gcloud run jobs execute --async --update-env-vars` does).
   The work order travels as an execution override. It's visible to anyone who can
   read the execution, but it's single-use and spent at registration, so a session
   that reads it gets nothing, the same reasoning as design.md. If that's not enough,
   write it to a GCS object only the runner's service account can read and pass the
   path instead.
   For a compute type (`BACKEND=compute`, today `android`), it creates
   `agent-<type>-<order id>` instead (`instances.insert`, the body from
   `gcp_instance_body` in `hooks/gcp/lib.sh`), with the work order and the runner's
   settings in instance metadata, and waits up to 40 s for the insert's operation:
   quota and capacity errors arrive there, not on the request.
4. Any failure after the marker deletes the marker (the orchestrator has
   `storage.objectUser` on the bucket for that) and exits 1, or 2 when the API says
   403 or 404: no job or image family, or no permission, which retrying won't fix.
   Compute Engine quota, capacity and rate limits exit 1 whatever their status; a VM
   that already exists for the order exits 0.

Orchestrator flags: `--expected-spawn-seconds` set from measured cold starts (multi-GB
GPU images are minutes, not seconds), `--hook-timeout 60`, `--shutdown-drain-sec 70`
with the host's 85 s stop budget (the orchestrator asks for drain + 15). Runner flags
in `claude.sh`: `--capacity 1`, `--release-idle-session-min`,
`--kill-session-after-min` below the job's task timeout, `--exit-if-unused-min`, and
`--retire-at`, which the entrypoint sets at start to the task deadline minus a
margin, so Cloud Run's hard stop releases sessions cleanly instead of losing them.
GPU jobs run up to 12 hours (preview since 2026-10-30).

## The orchestrator container

One container image, `agent-orchestrator` (`images/cloud/orchestrator/`), runs every
type's orchestrator. Claude's orchestrator takes one `--environment-secret-file`, so
it's one process per type inside one container:

- **Contents:** Claude Code, `curl`, `jq`, `hooks/gcp`, every `infra/gcp/*.env`, and a
  supervisor script as the entrypoint. No `gcloud` (Python, around 100 MB a run): the
  hook and the supervisor call REST APIs with a metadata-server token. Small, CPU
  only, and it never runs session code.
- **Types:** the supervisor reads every `infra/gcp/*.env` baked into the image except
  `project.env` and starts `claude self-hosted-runner orchestrator` for each, with
  `RUNNER_TYPE`, that type's `--min-idle` and `--expected-spawn-seconds`, its own
  `--health-port` (8080 upward), and `--shutdown-drain-sec 70`.
- **Secrets:** each type's `claude-env-<type>`, read from Secret Manager at start by
  the host's service account and written to a file per type (mode 600, `/dev/shm`). The
  supervisor passes each orchestrator only its own, and the hook unsets it before
  doing anything else. A type whose secret can't be read is retried; the others
  start anyway.
- **Supervision:** restart any orchestrator that exits; forward `SIGTERM` to all of
  them; prefix each one's log lines with its type. Every 5 minutes it logs each
  orchestrator's memory (`Pss` from `/proc/<pid>/smaps_rollup`, with its child
  processes) and whether its `/healthz` reports `connected`, so the host's memory is
  measured from Cloud Logging, with no SSH.
- **Reaper:** for each compute type, once a minute (in the background, so a slow
  API call can't hold up a signal): delete its stopped VMs and any past
  `MAX_RUN_DURATION_SEC` plus 5 minutes, and remove the `work-order` key from VMs
  whose guest has marked it read. The deploy's smoke VMs are left to the deploy.
  Logged as `supervisor: reaper`.
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
[gcp-infrastructure.md](gcp-infrastructure.md).

The jobs need no Terraform here: the deploy workflow writes each job from its
`infra/gcp/<type>.env` through the Cloud Run API, which is idempotent, and deletes any
`agent-*` job labelled `managed-by=agent-images` whose env file is gone. A second Terraform in this repo (its state next to
`lolay/infrastructure`'s, applied by the deploy workflow with the new digest as a
variable) becomes worth it if the workloads grow past jobs: the Android type's
Compute Engine templates and standby pools are the likely trigger.

Terraform's outputs (project, registry path, federation provider, service accounts,
bucket, VM) are committed here as a copy in `infra/gcp/project.env` for the
workflows, per the CI configuration rule: non-secret, reviewed, and Terraform stays
the source. It holds those outputs only; per-type settings go in the type's env
file.

The GitHub side is set by hand: the lock-bot App, the `production` and `lock-bot`
environments, and the `main` ruleset (see [The lock bot](#the-lock-bot)).

## Storage

Three places a session can put files, from fastest and shortest-lived to slowest and
longest-lived. Every runner gets the same three paths, so sessions and the tests
don't care which type they're on:

| Path | What it is | Lives | Size |
| --- | --- | --- | --- |
| `$AGENT_SCRATCH` (`/scratch`) | Cloud Run ephemeral disk: block storage dedicated to the instance | Until the execution ends | Per type, above |
| `$AGENT_OUTPUTS` (`/mnt/outputs/<type>/<date>/<session id>`) | Cloud Storage bucket `lolay-agents-outputs`, mounted read-write with Cloud Storage FUSE | 30 days, then the bucket's lifecycle rule deletes it | Unbounded |
| `$AGENT_DATASETS` (`/mnt/datasets`) | Cloud Storage bucket `lolay-agents-datasets`, mounted read-only with Cloud Storage FUSE (a file cache on `/scratch` comes with `gis`, where it matters) | Until removed by hand | Unbounded |

- **Scratch** is where work happens: the repo checkout (Claude's `--base-dir` is
  `/scratch/workspace`), downloads, intermediate files, builds. Without it, Cloud
  Run's filesystem is in memory, and every file written takes memory from the
  session. Each volume holds 1 to 100 GiB, up to ten per instance, and it's gone when
  the execution ends, crash included. Every type gets 10 GiB for now, the default
  quota per instance, which needs no approval; the region's 100 GiB is granted on
  first use. The larger GIS and GPU sizes above need a quota increase
  (`lolay/infrastructure`). `TMPDIR` and the caches (`XDG_CACHE_HOME`, pip, Homebrew)
  point at scratch too.
- **Outputs** is for anything worth keeping that's too big for git: dense point
  clouds, trained models, renders, rasters. Each session writes under
  `<type>/<UTC date>/<session id>/`, so a listing reads by type and day; you read
  results with `gcloud storage` or the console. Cloud Run mounts it as the
  container's user (uid 1000, directories 777, files 666) with implicit directories,
  so a session sees folders other sessions made. Cloud Storage FUSE isn't a full POSIX
  filesystem (no locking, slow small random writes), so a session works on scratch
  and copies finished files to outputs, rather than working in place.
- **Datasets** holds inputs worth reusing between sessions: a cached Sentinel-2 or
  3DEP subset, a photogrammetry image set, model weights. Read-only to sessions; you
  add to it with `gcloud storage cp`. The FUSE file cache on scratch makes a second
  read of the same file local.
- **Android** VMs get a 100 GB boot disk instead of an ephemeral volume, and the same
  two buckets through `gcloud storage` (no FUSE mount needed on a VM).
- **Memory** is the fourth option, already there: an RTX PRO 6000 instance has at
  least 80 GiB, so a working set of a few GiB in `/dev/shm` costs nothing extra.
- **Not proposed:** Filestore (NFS). It's fully POSIX and shared, but the smallest
  instance is a fixed monthly cost far above everything else here combined.

**Per session already.** Scratch needs no naming convention: every instance serves
exactly one session (`--capacity 1`, then it exits), and its ephemeral disk is
created with it and deleted with it, so `/scratch` is that session's and nobody
else's. Only the shared bucket needs the `<type>/<date>/<session id>` prefix. A
standby starts before it has a session, so the entrypoint can't know the id; a
`SessionStart` hook in the image's `~/.claude/settings.json` creates the session's
folder under `/mnt/outputs` and exports `AGENT_OUTPUTS` through `$CLAUDE_ENV_FILE`.
The session id comes from the session's own environment
(`CLAUDE_CODE_REMOTE_SESSION_ID` in Anthropic's cloud sessions; m1 confirms the
runner sets the same).

**How the agent knows.** Two ways, both baked into the image, like the Linux image's
`~/.claude/CLAUDE.md` describing the emulator today:

- A "This machine" `CLAUDE.md` for the cloud images, read at the start of every
  session:

  ```
  ## Storage
  - Work in $AGENT_SCRATCH (/scratch): a dedicated disk, deleted when this session
    ends. Your repo is checked out there. Don't write large files elsewhere: the
    rest of the filesystem is in memory.
  - Keep anything over 10 MB worth keeping in $AGENT_OUTPUTS (a Cloud Storage
    bucket, kept 30 days). Finish files on scratch, then copy them there; tell the
    user the gs:// path.
  - Read shared inputs from $AGENT_DATASETS (read-only).
  - Code still goes to git: commit and push before the session ends.

  ## Cost and time limit
  - This runtime costs about $AGENT_COST_USD_PER_HOUR an hour while the session
    runs, and the session ends at a set time (the start-of-session facts give both).
    Before starting work you expect to take over an hour, say what it will roughly cost and whether
    it fits before the end time. Commit and push well before the end.
  ```
- The same `SessionStart` hook prints one line of facts into the session's context:
  free space on scratch, the outputs path as `gs://...`, and what's at the top of
  datasets.

**Session notice: cost and time limit.** Every session opens by saying what its
runtime costs and when it will end, for example:

```
⚠ This runtime costs approximately $8.99/hour.
⚠ This session will time out after 60 minutes of inactivity, and ends by 6:42 PM PST
at the latest (in 7 h 52 min). Push your work before then.
```

A `CLAUDE.md` can't print anything on its own (it's instructions to Claude, not
output), so the same `SessionStart` hook does it two ways:

- `systemMessage` in its JSON output, which Claude Code shows to the user as a
  warning line;
- `additionalContext` carrying the same line, so Claude knows the limits too.

m1 found claude.ai shows the `systemMessage` line, so the `CLAUDE.md` rule that had
Claude repeat it as its first line is gone: it only doubled the notice.

m1 prints the time-limit line only. The cost line needs the deploy's price lookup,
which comes after m1; until `AGENT_COST_USD_PER_HOUR` is set, the hook leaves it out
and the session test marks it skipped.

The end time is worked out at session start, by the hook, because it depends on when
the runner started, not only on settings. The session ends at the earliest of:

- the session's own limit, `--kill-session-after-min` (480 by default, set per type);
- the runner's deadline, which the entrypoint sets as `--retire-at` from the job's
  task timeout (12 hours for GPU jobs) minus a margin, and exports as
  `AGENT_RETIRE_AT`. A standby that waited an hour for its session has an hour less,
  which is why the hook reads the real deadline instead of assuming the full limit;
- the inactivity timeout, `--release-idle-session-min` (60): after that long with
  no messages, the runner lets the session go. It resumes on a fresh runner at the
  next message, but anything unpushed is gone, so the notice leads with it. The
  minutes come from the type's settings, never hardcoded.

The time shows in `AGENT_NOTICE_TZ` (in each type's env file,
`America/Los_Angeles`), with the time remaining beside it. Near the end, the runner's
own `--retire-at` handling releases the session cleanly after its current turn; the
Stop hook that already asks Claude to push covers the rest.

The figure is worked out at deploy, not baked into the image, because it depends on
the job's resources (CPU, memory, GPU, scratch disk), which change in
`infra/gcp/<type>.env` without an image build. Every deploy, which includes every
image build and the nightly one:

1. reads the type's resources from its env file;
2. looks up the current prices for them in `us-central1` from the Cloud Billing
   Catalog API (Cloud Run CPU, memory, GPU and ephemeral disk SKUs; Compute Engine
   N2 cores, memory and balanced disk for `android`), with the workflow's federated
   identity;
3. sets two environment variables on the job (or the `android` VM's metadata):
   `AGENT_COST_USD_PER_HOUR` (`8.99`) and `AGENT_COST_DETAIL` (each part's share and
   the date the prices were read);
4. writes the same figures into the image manifest and the job summary, so a price
   change shows up in the deploy log.

The figure is the runtime's list price per hour of the session, rounded up to the
cent. It leaves out Claude usage itself (billed to the plan, not GCP), the free tier,
egress, and the buckets, and the notice says "approximately" for that reason. If the
price lookup fails, the deploy keeps the last figure and flags it in the job summary
rather than failing.

**Idle cost.** Close to nothing:

| Storage | Billed for | Cost with no sessions |
| --- | --- | --- |
| Scratch (ephemeral disk) | Provisioned size, only while an instance runs | $0: every type runs at `--min-idle 0` |
| Outputs bucket | Bytes stored, about $0.02 per GB a month (Standard, `us-central1`) | Whatever the last 30 days left behind; $0 once the lifecycle rule empties it |
| Datasets bucket | Bytes stored, same rate | What you put there: 100 GB is about $2 a month |
| Reads and writes | Operations, and FUSE's listing calls | Fractions of a cent per session; reads in the same region transfer free |

This changes one earlier decision: sessions now have GCP access, limited to these two
buckets. Each execution's service account, `agent-runner`, can write
`lolay-agents-outputs` and read `lolay-agents-datasets`, and nothing else, and every
session can see every other session's outputs. Fine for one owner; revisit if other
people get sessions on these runners. Nothing secret goes in either bucket.

## The head

A virtual head: Xvfb, OpenGL through VirtualGL/EGL on the GPU, screenshots for
computer use, noVNC if you want to watch. The open question is whether Cloud Run's
mounted driver includes the graphics libraries (`libEGL_nvidia`, `libGLX_nvidia`,
Vulkan ICD) or only compute. The docs only describe the compute path. That's the first
question m3 answers, because it decides between Cloud Run and the Compute Engine path
below. Software rendering
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

## Compute Engine VM per session: `android`, and the GPU fallback

Cloud Run gives a container no KVM, so `android` runs as a Compute Engine VM per
session, the cloud version of the Linux host's LXD VMs. The same path is the GPU
fallback if Cloud Run can't render, and the answer for any type that needs Docker in
the session or a full GPU desktop. Built in m2
([handoffs/plan-gcp-android-kestrel.md](handoffs/plan-gcp-android-kestrel.md)); not yet
deployed.

- **Image:** Packer's `googlecompute` builder (`images/android/gce`), run by the
  deploy on `main`: a build VM with nested virtualization, no external IP (Packer
  reaches it through IAP, tag `packer`) and no service account. `provision.sh`
  creates the `ubuntu` user (Ubuntu's Compute Engine image has none) and runs the
  LXD image's own provisioners (`images/android/scripts`, from the same staged
  layout), which boot the emulator once for its quickboot snapshot; then it adds the
  guest pieces in `gce/files`, and fails the build on any provisioner stderr. The
  image lands in family `agent-android-next`; `deploy.sh vm-promote` boots a smoke VM
  from it and only then moves it to `agent-android` (`images.patch` takes `family`),
  deleting the type's older images, keeping one.
- **Smoke VM:** metadata `agent-smoke=true` and no service account. The guest runs
  `agent-gce-smoke` (Claude, sudo, `kvm-ok`, Java, adb, scratch, and the emulator
  booted twice from its snapshot: once from a disk Compute Engine is still restoring
  from the image, which reads lazily, and once warm), reports through guest
  attributes, and powers off. The deploy prints the log and puts the boot times in
  the run's summary, so every image build measures the emulator.
- **Machine:** from `infra/gcp/android.env`: `n2-standard-8` (nested virtualization
  needs an Intel series or AMD `n4d`), a 100 GB `pd-balanced` boot disk, no external
  IP (egress through Cloud NAT), the `agent-runner` service account, and
  `maxRunDuration` 9 h 30 min with `instanceTerminationAction` `DELETE` as the
  backstop. `automaticRestart` is off: a VM that dies stays dead and is reaped.
- **Hook:** after the order marker, `agent-android-<order id>` (the whole id, so two
  orders never share a name) from the family's latest image, with metadata
  `work-order`, `runner-env` (label, base dir, retire time, the runner limits) and
  guest attributes on.
- **Guest:** `agent-gce-order.service` runs once at boot as root: it writes the work
  order to `~ubuntu/.claude-runner/environment-secret` (600), writes `runner.env`
  from `runner-env` (plain `KEY=value` lines only) with `EPHEMERAL=true`, which
  starts `agent-runner` as on the LXD VMs: one session, then power off. It marks the
  order read in a guest attribute rather than deleting the metadata key: deleting it
  would need `compute.instances.setMetadata` on `agent-runner`, and granted
  project-wide that would let one session rewrite another session VM's
  `startup-script`. The key is spent at registration anyway, as the Cloud Run
  override is. Any failure, or no order, powers the VM off. `agent-debug=true`
  leaves a VM started by hand alone.
- **Cleanup:** the VM doesn't delete itself (that would mean a delete permission
  inside the session). The reaper in the orchestrator container does
  ([above](#the-orchestrator-container)).
- **Logs:** the guest's journal goes to the serial console (`ForwardToConsole`), and
  the VM sets `serial-port-logging-enable`, so it reaches Cloud Logging after the VM
  is gone, unless the org policy `compute.disableSerialPortLogging` is enforced.
- **Storage:** `$AGENT_SCRATCH` is `/scratch` on the boot disk. `$AGENT_OUTPUTS` and
  `$AGENT_DATASETS` aren't mounted on the VMs yet (open item).
- **Warm starts:** none at first (`--min-idle 0`); a managed instance group standby
  pool of suspended VMs later if cold starts hurt.

## Milestones

Linux first and tested, then Android and GIS, then the GPU types. The orchestrator
host starts as an `e2-micro`, which holds the first three types' orchestrators, and
moves to `e2-small` with the GPU milestone.

### m1 - `linux`

The whole path for one type, end to end:

- `images/cloud/base`, `images/cloud/linux`, and the deploy workflow from `main`:
  build, push, smoke through `agent-linux-next`, promote; the build-only check on pull
  requests.
- `hooks/gcp/spawn-runner` (Cloud Run), the orchestrator container with its
  supervisor, and the `e2-micro` host from `lolay/infrastructure` (applied).
- No standby, no cost line, Claude upgrading itself at start (all above).
- Plan: `specs/handoffs/plan-gcp-linux-heron.md` (removed once m1 was done; in git history at `f2bd141`).
- The "Agent: Linux" environment in claude.ai and `claude-env-linux` in Secret
  Manager.
- Done when [tests/sessions/linux.md](../tests/sessions/linux.md) passes, run twice
  so the isolation check sees a fresh runner the second time, and the orchestrator's
  memory on the host is measured.

#### m1 results

Done 2026-11-05: [lolay/agent-images#13](https://github.com/lolay/agent-images/pull/13),
then #14 (no sudo), #15 (toolchains in the image, by Gary) and #16 (PATH in every
shell). Both test runs passed every check that can pass: the second on a fresh
runner (no isolation marker), with nothing missing from the image. What they and the
deploys found:

| Finding | Result |
| --- | --- |
| Scratch on Cloud Run jobs | `emptyDir` `medium: DISK` works: ext4, 10 GiB, 210 MB/s each way (looks like a provisioned cap) |
| Root | None: Cloud Run sets no-new-privileges, so `sudo` and apt fail. Sessions run as `ubuntu` and install with Homebrew (#14); the image carries Chromium's libraries for Playwright (#15) |
| Buckets | Outputs round-trips (about 60 MB/s write through FUSE); datasets is read-only. Cloud Run mounts both as the container's uid 1000 by default; explicit `uid`/`gid` options got uid 2000 |
| Cold start | About 55 s for the 5.5 GB image (Cloud Run streams layers). `EXPECTED_SPAWN_SECONDS` stays 180, about three times that |
| CPU | `nproc` reports 5 for a 4 vCPU job; the ripgrep release build (33 to 60 s) used under 3 |
| Session id | `CLAUDE_CODE_REMOTE_SESSION_ID` (`cse_...`), as in Anthropic's own cloud sessions |
| Session notice | The time line is right (the earlier of 8 h from session start and the runner's retire time) and claude.ai shows the hook's `systemMessage` |
| Orchestrator memory | 78 to 84 MiB Pss on the `e2-micro` (see "Memory, measured" above) |
| Hostname | `localhost` in every execution; the test's isolation marker records the session id to tell runs apart |

### m2 - `gis` and `android`

- First, toolsets ([image-toolsets.md](image-toolsets.md), s1 to s5), so `gis`
  and `android` each get their own tool list rather than the Android image's.
- `gis`: `images/cloud/gis` (GDAL, PDAL, QGIS), its job and environment. Done when
  [tests/sessions/gis.md](../tests/sessions/gis.md) passes.
- `android`: the Compute Engine path above: Packer image, the hook's Compute Engine
  branch, the reaper (Cloud NAT is already there). Done when
  [tests/sessions/android.md](../tests/sessions/android.md) passes, with the emulator
  boot time compared against the Linux host's. Plan:
  [handoffs/plan-gcp-android-kestrel.md](handoffs/plan-gcp-android-kestrel.md); built,
  waiting for its first deploy.

### m3 - GPU types

- Host to `e2-small`.
- `linux-gpu` on an L4 first (cheaper), then RTX PRO 6000. Done when
  [tests/sessions/linux-gpu.md](../tests/sessions/linux-gpu.md) passes, which settles
  the open graphics question: if OpenGL and Vulkan don't run on the GPU in Cloud Run,
  GPU types move to the Compute Engine path (G4).
- `3d-gpu`: COLMAP, Blender and the splatting library built for compute capability
  12.0. Done when [tests/sessions/3d-gpu.md](../tests/sessions/3d-gpu.md) passes.

### m4 - Dependency watch

The lock files, the hourly watch, the lock bot, the nightly rebuild, the cleanup
policy out of dry run, and manifests; with the watch in place, turn off Claude's
self-update at start (`CLAUDE_UPGRADE=false`). Independent of the types; it can start
any time after m1.

## Open items

| Item | Why |
| --- | --- |
| Graphics libraries on Cloud Run GPUs | Decides whether a virtual head runs on Cloud Run |
| Cold start of a multi-GB GPU image | Sets `--expected-spawn-seconds` and whether a standby is worth paying for |
| Cloud Run GPU quota grant | New projects get a default grant; may need a request for RTX PRO 6000 |
| Emulator speed under nested virtualization on Compute Engine | Decides whether `android` stays on GCP or on the Linux host; every image build's smoke VM measures it, then m2's session test |
| Outputs and datasets on the `android` VMs | gcsfuse mounts like the Cloud Run types'; left out of m2, since its test doesn't use them |
| Serial port logging for the `android` VMs | Their journal reaches Cloud Logging only if `compute.disableSerialPortLogging` isn't enforced; m2's first deploy shows which |
| Data | Keep data-heavy work next to wherever its data is: cross-cloud egress is about $0.08 to $0.12 per GB |

## References

- [Claude Code on-demand runners](https://code.claude.com/docs/en/self-hosted-environments-configuration)
- [GCP network egress pricing](https://cloud.google.com/vpc/network-pricing)
- [EC2 Mac instances](https://aws.amazon.com/ec2/instance-types/mac/), [Tart on AWS Marketplace](https://tart.run/blog/2023/10/06/tart-is-now-available-on-aws-marketplace/), [Tart issue on headless mac2.metal](https://github.com/cirruslabs/tart/issues/1146)
- [Cloud Run GPU jobs](https://docs.cloud.google.com/run/docs/configuring/jobs/gpu), [execute jobs with overrides](https://docs.cloud.google.com/run/docs/execute/jobs), [worker pools](https://docs.cloud.google.com/run/docs/configuring/workerpools/gpu), [release notes](https://docs.cloud.google.com/run/docs/release-notes)
- [MIG standby pools](https://docs.cloud.google.com/compute/docs/instance-groups/suspended-and-stopped-vms-in-mig)
