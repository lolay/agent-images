# GCP runners

Status: **m1 (`linux`) done, 2026-11-05**: sessions run on Cloud Run, tested twice
([m1 results](#m1-results)). Written 2026-11-04; the foundation is applied
(`lolay/infrastructure`, `stacks/gcp/agents`). **m2 done, 2026-11-06**: `gis`
([m2 `gis` results](#m2-gis-results)) and `android`, a Compute Engine VM per
session ([m2 `android` results](#m2-android-results)). m3, the GPU types, is under
way ([handoffs/handoff-m3-gpu-types.md](handoffs/handoff-m3-gpu-types.md)): the
`gpu` toolset and `linux-gpu` on an L4 are built, waiting for their first deploy.
Recheck anything marked preview before building on it.

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
| Image | `agent-<type>`, built `FROM` `agent-base` (Ubuntu, or CUDA's devel image for `-gpu` types: bake target `base-gpu`) plus the type's tools |
| Backend | A Cloud Run job (`agent-<type>`) with its resources, or a Compute Engine template |
| Orchestrator settings | `--min-idle`, `--expected-spawn-seconds`, runner flags |

| Type | Image | Backend and resources | Scratch disk | Standby |
| --- | --- | --- | --- | --- |
| `linux` | `agent-linux` (`agent-base` as is) | Cloud Run job, 4 vCPU, 16 GiB | 10 GiB | 0: a standby holds 4 vCPU and 16 GiB, about $270 a month; 1 later if cold starts hurt |
| `linux-gpu` | `agent-linux-gpu` (`linux`'s toolsets on CUDA's devel image, then `gpu`) | Cloud Run job, RTX PRO 6000, 20 vCPU, 80 GiB (that GPU's minimum); an L4 (4 vCPU, 16 GiB) while m3 tests it | 10 GiB while m3 tests the L4, 100 GiB with the RTX PRO 6000 | 0: a standby GPU bills the whole time |
| `gis` | `agent-gis` (toolsets core, native, node, gis: GDAL, PDAL, QGIS from conda-forge) | Cloud Run job, 8 vCPU, 32 GiB (Cloud Run's most memory; gis is bound by memory, not CPU) | 100 GiB | 0: a standby is about $550 a month |
| `3d-gpu` | `agent-3d-gpu` (from `agent-base-gpu`: COLMAP built for Blackwell, Blender) | Cloud Run job, RTX PRO 6000 | 200 GiB (two volumes) | 0 |
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
Adding a type is:

1. `infra/gcp/<type>.env`. The env files are the one list of types: the supervisor,
   the deploy and the pull request check all read it.
2. `images/cloud/<type>/Dockerfile`, `FROM agent-base` (or `agent-base-gpu`, or
   another type's tools, as `linux-gpu` is `FROM agent-linux`).
3. Two targets in `images/cloud/docker-bake.hcl`: `<type>-tools` (that Dockerfile,
   with its parent as a named context) and `<type>` (inheriting `_runner`, with
   `agent-tools = "target:<type>-tools"` and `tags("<type>")`). `tests/unit/test_bake.py`
   fails until both exist, and checks that `changed-targets.sh` sees the same parents.
4. The environment in claude.ai, and its key as secret `claude-env-<type>`.

The pull request builds it with the other types; the push to `main` deploys it.

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
so no type inherits another's. The GPU types' parent, `base-gpu` (m3), is
`agent-base`'s Dockerfile on CUDA's devel image rather than runtime.

We manage them, in this repo, next to the macOS and Linux images, and they share
`images/shared` so sessions behave the same everywhere.

```
images/cloud/
  base/Dockerfile      ubuntu:24.04 through mirror.gcr.io, the runner user, and the
                       core toolset; a build parent only. Bake target base-gpu is
                       the same on CUDA 13.0 devel (BASE_IMAGE), for the -gpu types,
                       which add the gpu toolset (VirtualGL, Vulkan, EGL) last
  <type>/Dockerfile    FROM agent-base, agent-base-gpu or another type's tools
                       (linux-gpu is FROM agent-linux, which bake builds on
                       base-gpu), adds the type's toolsets, one layer each (bake
                       target <type>-tools)
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
- **Claude upgrades itself at start, on the weekly types for good.** A `nightly`
  type ([Rebuild cadence](#rebuild-cadence)) is at most a day behind today, and
  within the hour once m5's watch rebuilds it on a release; m5 then turns its
  self-update off. A `weekly` type can be a week behind, so it keeps
  `CLAUDE_UPGRADE=true` (the shared default): about 6 s per start, against a rebuild
  that costs up to 45 minutes. The image still installs Claude in its own last layer.

Layer order, slowest-moving first, so a rebuild only redoes what changed:

```
FROM <base>@<digest>      ubuntu:24.04, or nvidia/cuda 13.0 devel for -gpu,
                          both through mirror.gcr.io
core toolset              apt, Homebrew, the shared session tools (agent-base)
type toolsets             one layer each: apt at the locked snapshot, Brewfile,
                          install.sh (images/cloud/<type>)
images/shared files       and the session files (images/cloud/runner)
Claude Code               ARG CLAUDE_VERSION; RUN install that exact version
versions report           /etc/agent-images/versions.md (m4)
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

- The deploy workflow triggers on `push` to `main`, `schedule` (which GitHub runs on
  the default branch, `main`) and `workflow_dispatch`, and its jobs run in a GitHub environment, `production`, whose deployment branch policy
  allows only `main`.
- The workload identity provider's attribute condition accepts only this repo's
  numeric id with `ref` `refs/heads/main` and that environment, so a token from any
  other branch, pull request or fork can't get GCP credentials at all.
- Pull requests that touch the images run a check with no push and no GCP
  credentials. It builds every type `changed-targets.sh --all` lists (`make
  cloud-targets`) and the orchestrator, so a new env file's image is built before
  it reaches `main`. Each runner type is also loaded and run through `make
  cloud-smoke` (the binary checks and smoke tests, `smoke pr`, as in m4), and its
  versions report goes to the run's summary and an artifact. The check runs on
  every pull request, and its `gate` job is what `main`'s ruleset requires: it
  passes when nothing the check covers changed, or when the lint, the tests and
  every image's build and smoke passed.

### What a push to `main` deploys

Any push to `main` that changes what a GCP runtime runs redeploys that runtime, and
only that one. The workflow's first job diffs `github.sha` against the head of the
last successful push deploy (not `github.event.before`: GitHub's concurrency queue
keeps one pending run and cancels the others, so their pushes would never be
deployed) against this map, and hands the affected targets to the deploy jobs as a
matrix. A failed deploy makes the next one redeploy more, never less:

| Path changed | Redeploys |
| --- | --- |
| `images/shared/**`, `images/toolsets/install-toolset.sh`, `images/toolsets/write-claude-md.sh`, `images/toolsets/toolset-checks.sh`, `images/toolsets/write-versions.sh`, `images/toolsets/skills/**`, `images/cloud/base/**`, `images/cloud/runner/**`, `images/cloud/entrypoint.sh`, `images/cloud/docker-bake.hcl`, `.dockerignore` | Every runner type |
| `images/toolsets/<toolset>/**` | The types whose Dockerfiles (or build parents') install that toolset |
| `images/cloud/<type>/**`, including its `versions.lock` | That type |
| `hooks/gcp/**`, `images/cloud/orchestrator/**` | The orchestrator host (new image, then the host restarts its container) |
| `infra/gcp/<type>.env` (CPU, memory, GPU, timeout, `--min-idle`, `REBUILD`, `DEPLOY`) | That type's job and orchestrator settings, no image build. `DEPLOY=false` builds and pushes the image but writes no job, runs no smoke and starts no orchestrator: a type waiting for its quota. It is for Cloud Run types only: deploy.sh's vm commands and the supervisor refuse it for `BACKEND=compute` |
| `scripts/cloud/job-spec.sh` | Every type's job, no image build |
| `images/android/**` (the LXD image's scripts and files, and `gce/`), the toolsets in `images/android/toolsets.txt`, `images/shared/**`, the four toolset scripts and `images/toolsets/skills/**` | The `android` Compute Engine image: Packer build, smoke VM, then family `agent-android` ([below](#compute-engine-vm-per-session-android-and-the-gpu-fallback)) |
| `infra/gcp/android.env` | The orchestrator only: the hook reads it at each spawn, from the orchestrator image |
| Anything else (`specs/`, the macOS image, README) | Nothing |

The `FROM` chain comes from the Dockerfiles themselves, so adding a type doesn't mean
editing the map. `agent-base-gpu` has no directory (it's bake target `base-gpu`,
`base`'s Dockerfile on CUDA), so the chain reads it as `base`; `test_bake.py` checks
the chain against the bake file's. `infra/gcp/project.env` changes everything. When the diff can't be
computed (no earlier successful deploy, or its commit is gone after a force push), it
deploys everything. A manual dispatch takes a list of targets, or `all`.

### The lock

Each Cloud Run type has a lock in git, `images/cloud/<type>/versions.lock`: every
dependency resolved to an exact version. It's the record of what the live image was
built from, and the check compares upstream against it, so nothing ever reads the
image. `scripts/cloud/lock.sh` is its one reader and writer: `resolve <type>`
(Linux, with Docker and Homebrew), `bake-args`, `diff`, `summary` and `pins`. A lock is
`<kind> <name> <value>` lines, sorted, under a header:

```text
apt ca-certificates 20260816ubuntu0.24.04.2
apt-snapshot - 20261106T222014Z
base - mirror.gcr.io/library/ubuntu:24.04@sha256:fe8156...
brew lolay/tap/nowline 0.4.0
claude - 2.1.319
release tflint v0.65.0
rust - 1.99.1
terraform - 1.17.1
```

`apt-floating` marks a package from a source with no snapshot (NVIDIA's CUDA
repo; none today), and `pinned <what> <issue URL>` a pin
([below](#pinning-a-dependency)).

| Dependency | Locked as | Constraint, where it's declared | Resolved by (from upstream only) |
| --- | --- | --- | --- |
| Base image | Digest | `BASE_IMAGE` per base target in `docker-bake.hcl` (a tag; `@sha256` pins it) | `docker buildx imagetools inspect`: the index digest, no pull (a bare registry `HEAD` on mirror.gcr.io needs a token) |
| apt packages, dependencies included | An Ubuntu snapshot id, plus the full resolved list | `images/cloud/base/packages.txt` and each toolset's `packages.txt` (`name=version` pins one) | `apt-get install --simulate` of that list in a stock container at the locked base digest, against today's archive. A resolved set unlike the lock's (or a new base) moves the snapshot to now |
| Homebrew, dependencies and `lolay/tap` included | Each formula with its version and revision | The type's Brewfiles | `brew bundle list`, `brew deps --union` (no build-only dependencies) and `brew info --json=v2` on the runner's own Homebrew, after tapping and trusting the Brewfiles' taps. Matches the image's `brew list` exactly |
| Claude Code | Version | `CLAUDE_VERSION` in `docker-bake.hcl` (`latest`, `stable`, or a version) | `https://downloads.claude.ai/claude-code-releases/<channel>` |
| Rust toolchain | Version | `RUST_TOOLCHAIN` in `images/toolsets/rust/install.sh` (`stable`, a minor line, or a version) | rustup's channel manifest |
| Terraform | Version | `TERRAFORM_VERSION` in `images/toolsets/terraform/install.sh` (`latest`, `~> x.y`, or a version) | HashiCorp's release index, newest stable on that line |
| tflint, VirtualGL | Release tag | `TFLINT_VERSION`, `VIRTUALGL_VERSION` in their toolsets' `install.sh` | The tag GitHub redirects `/releases/latest` to |

The build consumes the lock (`scripts/cloud/bake.sh` locally and on pull requests,
the deploy's bake from `lock.sh bake-args`): `BASE_IMAGE` at the locked digest,
`APT::Snapshot` at the locked snapshot for every apt install, `CLAUDE_VERSION`,
`RUST_TOOLCHAIN`, `TERRAFORM_VERSION`, `TFLINT_VERSION` and `VIRTUALGL_VERSION`
from the lock, each `ARG` declared just before its own layer. A type with no lock
doesn't build. apt's snapshot makes the apt layer reproducible, old versions
included, which plain `pkg=version` can't promise because Ubuntu's update pockets
keep only the newest. One install isn't held: snapshot.ubuntu.com is HTTPS only and
the stock `ubuntu:24.04` image has no CA certificates, so the base first installs
`ca-certificates` (and, on plain Ubuntu, its `openssl`) from today's archive, then
sets the snapshot. The runner stage drops the setting, so a session's own apt
reads today's archive. The VM images (android) take each `install.sh`'s default
and have no lock (m5, D2).

Every `apt-get update` in a cloud build, and `lock.sh`'s resolve, runs with
`images/cloud/base/apt-build.conf`: `APT::Update::Error-Mode "any"`
(`--error-on=any`), so a part of the archive that fails to download fails the
update. apt's default only warns and carries on without it. A build missing
`noble-security` would install unpatched packages and pass its smoke, and a
resolve missing `noble-updates` would lock release versions. The base layer
installs the file for itself and the toolset layers, the runner stage drops it
with the snapshot, and the orchestrator build and the resolve pass it in
`APT_CONFIG`. No extra retries: apt already retries each download three times
with backoff. An outage longer than that is the workflows' to ride out (lock
pull requests retry hourly, a failed deploy is covered by the next push or
nightly build). Found on 2026-11-11, when snapshot.ubuntu.com was unreachable
and builds failed with "Unable to locate package sudo" rather than the fetch
error.

The resolve's one registry read, the base tag's digest, does retry, but only on
a 429: mirror.gcr.io answers one when Google's quota on its reads from Docker
Hub runs out, a limit that clears in minutes, unlike an outage. `lock.sh` waits
30, 60 and 120 seconds between attempts, the way its curl calls already retry
with `--retry 3`; any other failure, or a 429 after the last wait, fails the
resolve at once with the registry's error. Found on 2026-11-12 (lock-watch run
43141207783), when the 429 failed the watch with a bare exit 4 from `jq`.

Homebrew is the exception, and stays that way (owner, 2026-11-06): it only installs
current versions, so its lock entries are a record and a change trigger, never a pin.
A moved formula is what makes the watch propose a lock and so a build, but the build
installs whatever Homebrew has when it runs. Two consequences follow. The lock pull
request's build and the deploy's build can see different Homebrew versions if one
moved in between; the deploy's `-next` smoke is the gate for that. And a revert of
the lock brings apt, the base image, Claude, Rust and Terraform back exactly, but
Homebrew back current.

What an image actually got is its versions report
([m4](#m4---functional-smoke-and-versions-report)), `/etc/agent-images/versions.md`,
read from inside the built image. The lock says what was asked for; the report says
what landed, and the two differ only where Homebrew moved between them, or in the
base's certificate bootstrap. What neither the lock nor its constraints cover: the
Homebrew installer (`HEAD`) and `gis`'s conda-forge solve (pixi, with no
`pixi.lock`); the scheduled rebuilds pick those up.

The watch never touches GCP, so there's no egress. The stock Ubuntu image it starts
is about 30 MB, from a public registry.

### Watch: hourly for `nightly` types, weekly for all

`.github/workflows/lock-watch.yml`. Two schedules, both with `timezone:
America/Los_Angeles` (schedules take a time zone since March 2026): `17 8-22 * * *`
resolves the `nightly` types (`linux` today), and `47 4 * * 0`, 50 minutes before
the weekly rebuild, resolves every type, so a `weekly` type's lock moves once a week.
A dispatch takes `all`, `nightly` or `weekly`. One run at a time, queued. It never
deploys; it only proposes a pull request:

1. `resolve` (read-only `GITHUB_TOKEN`, no secrets) resolves each type in the class
   with `lock.sh resolve` into a candidate tree that starts as `main`'s locks.
2. If a lock pull request is open, the locks its branch changed for types this run
   didn't resolve are carried into the candidate (`lock-watch.sh carry`), so the
   hourly run never drops the weekly run's `gis` candidate. A lock `main` changed
   since the branch left it (a constraint change re-locked, or a rollback) is
   dropped, not carried, since the branch's copy is older than `main`'s and would
   revert it; that type's next resolve proposes afresh.
3. Nothing moved: done, green, nothing pushed (an open lock pull request is closed,
   since the candidate matches `main`: upstream hasn't moved, or step 2 dropped the
   only lock the pull request held). The open pull request already proposes
   exactly this on `main`'s head (`lock-watch.sh proposed`): nothing is pushed, so
   nothing new reruns its checks. A third job, `retry` (`GITHUB_TOKEN` with Actions
   write, no key), reruns the failed jobs of its `cloud-build` run
   (`lock-watch.sh retry`), up to four attempts in all, an hour apart. That rides
   out an upstream outage without rebuilding a broken candidate every hour: on
   2026-11-11 snapshot.ubuntu.com was unreachable for an afternoon, and before
   `retry` the red pull request it left sat until upstream moved again.
4. Something moved: `propose` (environment `lock-bot`) commits the moved locks onto
   `main`'s head as `lock/watch` and opens the pull request, or refreshes the open
   one, titled from `lock.sh summary` (`lock(linux): claude 2.1.318, go 1.26.1, apt
   3 packages`; several types read `lock(gis, linux): gis: ...; linux: ...`), with
   each type's `lock.sh diff` as its description, and turns on auto-merge (squash).
5. The pull request's required check is `cloud-build`'s `gate`: the build of every
   type, then `make cloud-smoke` on it, the functional smoke from
   [m4](#m4---functional-smoke-and-versions-report). Green merges it, and that push
   to `main` starts the deploy, which smokes once more at `-next` in Cloud Run before
   promoting.

The job summary lists every pin with its locked value, upstream's newest now and its
issue's state, read with `GITHUB_TOKEN` (the bot has no Issues permission); a closed
issue reads **closed**: remove the pin?

A version that breaks something never reaches `main`: its pull request stays open
and red once its retries are spent, which is the notification, and the next run that sees `main` or upstream
move refreshes the same pull request rather than opening another. Fix forward (a
toolset change on its own pull request) or pin ([below](#pinning-a-dependency));
either way the next watch run rebases the lock onto it.

The lock on `main` is the desired state, and the deploy makes Cloud Run match it. If
a deploy fails after merge (the Cloud Run smoke catching what the hosted runner
couldn't, such as the GPU or the mounts), `main` is ahead of what's live until it's
fixed: the failed run notifies, and the nightly build retries.

The pull request needs a GitHub App token, not `GITHUB_TOKEN`: a pull request opened
with `GITHUB_TOKEN` doesn't start other workflows, so its checks would never run. A
pull request rather than a direct commit (owner, 2026-11-06): the smoke gates the
version before it lands, and `main`'s rules apply to the bot like anyone else, so it
needs no bypass.

### The lock bot

With pull requests, the bot never writes `main`: it writes its own branch and asks,
and `main`'s ruleset (pull requests required with no approvals, squash only, `gate`
required from GitHub Actions, no force pushes or deletions, no bypass actors) holds
for it as for anyone. The ruleset is a repository ruleset, created 2026-11-07 with
m5; before that `main` had no protection, though this section said it did. What's left to guard is
what it can push to its branch, and that only lands through the required checks:

| Layer | What it stops |
| --- | --- |
| Installed on `lolay/agent-images` only; permissions Contents write, Pull requests write and Metadata read, nothing else | Any other repo; issues, settings, secrets |
| No Workflows permission | GitHub refuses any change from the App to `.github/workflows/`, so it can't rewrite the checks that gate it |
| Each run's token minted by `lock-watch.sh token` for this one repository with Contents and Pull requests write, and revoked when the job ends (valid an hour at most) | A leaked token outliving its run, or reaching another repository the App is later installed on |
| The App's private key is a secret of a GitHub environment, `lock-bot`, that only `main` can use, and only the `propose` job takes it | Any branch, pull request or fork using the key; and the key sharing a runner with the base images and apt runs `resolve` pulls from upstream |
| `propose` checks every file it was handed before calling GitHub: only `images/cloud/<type>/versions.lock`, no links (`lock-watch.sh guard`) | A bug in the resolver writing anything else, which would otherwise sit in an auto-merging pull request |

Commits made through the API (`createCommitOnBranch`) are signed by GitHub, so a
"require signed commits" rule works with the bot too. The commit is made on a
staging branch (`lock/watch-staging`, at `main`'s head, deleted after), and
`lock/watch` is then moved to it: moving `lock/watch` to `main` first would put the
pull request's head inside `main`, and GitHub would mark it merged.

The token is minted in the script (a JWT signed with `openssl`, then
`POST /app/installations/<id>/access_tokens` naming the repository and the two
permissions), not by `actions/create-github-app-token`: it's about 30 lines, and it
keeps the key out of a third-party action.

Expect one or two lock pull requests a day, each merging itself within the build's
time: Claude most weekdays, apt security updates a few times a week, Homebrew less.

### Deploy: build, smoke, promote

Runs only on `main`: a push, a scheduled rebuild or a manual dispatch, one type at a time
in a `concurrency` group that queues rather than cancels.

Layer caching through the Actions cache (`cache-to type=gha`), not the registry, so a
build reads nothing back from GCP. The cache holds 10 GB per repo, shared by every
type, and CUDA's layers alone are 4 GB, so `-gpu` types build without it (owner,
2026-11-06): about 12 to 15 minutes from scratch, measured locally for `linux-gpu`,
and only when something they use changed. The CPU types keep their caches.
Raising the repo's cache limit ($0.07 per GB-month over 10 GB) would let them
cache too. Hosted runners have about 14 GB
of free disk, so GPU builds clear the runner's preinstalled toolchains first, the
usual step for large images.

Every deploy follows the `-next` pattern from `build-image.sh`:

1. Push `us-central1-docker.pkg.dev/<project>/agent-images/<image>:<date>-<sha>`,
   where `<sha>` is the `main` commit being deployed. A change to the type's env file
   alone skips the build and reuses the live digest.
2. Point `agent-<type>-next` at that digest and run one smoke execution in Cloud Run
   (the entrypoint's `smoke` mode): `claude --version`, scratch is a real writable
   disk, outputs round-trips a file, datasets refuses a write, Homebrew's prefix is
   writable by the session user, and every toolset's binary checks and smoke tests
   (m4, "Binary checks and smoke tests"), which for `-gpu` types include
   `nvidia-smi`, a CUDA device probe and `eglinfo -B -p surfaceless`. It
   runs in GCP, next to the registry, so the pull is free.
3. Only if it passes, point `agent-<type>` at the same digest. Cloud Run resolves the
   image when the job is updated, so running sessions never see a half-built image.

Jobs are written through the Cloud Run v2 REST API (`PATCH` with `allowMissing=true`,
so the same call creates or updates), from a Job spec rendered from the env files by
`scripts/cloud/job-spec.sh`. `gcloud run jobs deploy` can't: its `--add-volume` has no
disk-backed type, while the API's `emptyDir` takes `medium: DISK`. Every job runs
with `maxRetries: 0` and one task: a retried task would come back with a spent work
order.

### Rebuild cadence

Not every image is worth rebuilding every night. Each type's `REBUILD` in
`infra/gcp/<type>.env` puts it on one of two schedules of the deploy workflow, a
no-cache build promoted the same way as a push's. A type without `REBUILD` is
`nightly`, so a new one is never left off. `scripts/cloud/changed-targets.sh
--schedule <class>` lists a class's targets.

| Image | Rebuild cost | `REBUILD` | Claude between builds |
| --- | --- | --- | --- |
| `linux` | Minutes; Claude is the last layer | `nightly`: 04:07 Pacific, Tuesday to Saturday, after the weekday's Claude releases | Locked: the watch moves it within the hour, and sessions don't update it (`CLAUDE_UPGRADE=false`, m5) |
| Orchestrator | A minute or two | Rides `nightly` (not a type, so no env file) | Baked in; rebuilt nightly |
| `gis` | 9 GB, most of the Actions cache's 10 GB; conda-forge moves slowly | `weekly`: 05:37 Pacific, Sunday | Self-updates |
| `android` | About 45 min of Packer with no layer cache, the disk capture and a smoke VM; about $0.50 in GCP | `weekly` | Self-updates |
| `linux-gpu`, `3d-gpu` (m3) | About 12 to 15 min from scratch; `-gpu` types build without the Actions cache (CUDA is 4 GB of it) | `weekly` | Self-updates |

The trade is build cost against how fast an image's contents move and what being
behind costs a session. Claude, the thing that moves most, is locked on `nightly`
types: the hourly watch proposes each release, and `gcp_runner_settings` sets
`CLAUDE_UPGRADE=false` for them (read from `REBUILD`, so a type moved to `weekly`
gets self-update back). On `weekly` types it updates itself at runner start, so a
week-old `android` or `gis` image still runs today's Claude; what a week costs it is apt and Homebrew updates, which a session with
`sudo` (`android`) or Homebrew (all) can pull when it needs one. Every type still
rebuilds on any push that touches its inputs ([above](#what-a-push-to-main-deploys)).
`android` included: a runner fix in `images/shared` reaches every type, not only
the cheap ones.

A scheduled run catches what the lock can't see (a curl installer), retries a failed
deploy, and keeps the "track latest" promise even if a check is wrong. Before m5 it's
the only way apt and Homebrew updates reach an image that nothing has pushed to.
With m5, every scheduled build builds from the lock on `main`, without the
layer cache: it moves nothing the lock pins (the base, apt, Claude, the
toolchains), and picks up what it can't (Homebrew, the installers). The hourly
watch covers `nightly` types; a weekly watch run covers every type, so a `weekly`
type's lock moves once a week.

A scheduled run can displace a push run waiting in the deploy's `concurrency` queue
(GitHub keeps one pending run); the next push covers it, since `plan` diffs against
the last successful push deploy.

The macOS image (Tart) and the Linux host's LXD image stay manual, `make build` on
an Xcode or OS release or about monthly; both upgrade Claude at VM start.

Deferred: splitting `android` into a base image (toolsets, SDK, the emulator's
snapshot) and a thin top (`images/shared`, Claude), so a runner fix doesn't redo
the SDK. Worth it only if pushes to `images/shared` keep triggering `android`
builds; the 100 GB disk capture still costs every build.

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
  newest. Rollback is a pull request reverting the lock on `main`, which deploys like
  any other commit: apt, the base, Claude, Rust and Terraform come back exactly
  (snapshot, digest, versions), Homebrew comes back current. A revert alone lasts
  only until the next watch re-proposes the newer version, so pair it with a pin on
  whatever broke (below).
- **History in git, reports on GitHub.** The lock's history is the permanent record
  of what every image was built from, and `git log -p images/cloud/3d-gpu/versions.lock`
  shows what changed and when. Each deploy's versions report
  ([m4](#m4---functional-smoke-and-versions-report)) is what it actually got, Homebrew
  included (android's too, downloaded from its Packer build VM), in the run's summary
  and as an artifact for 90 days (a public repo's
  maximum), and in the image itself for as long as the registry keeps it. All public,
  which is fine: builds hold no secrets.

Make targets in the same vocabulary as the rest: `make cloud-build` (build the
images locally), `make cloud-smoke` (run the entrypoint's smoke mode against local
directories), `make cloud-test` (the unit tests), `make cloud-ci` (what the pull
request check runs), `make cloud-status` (read-only: what each job and the host run),
and from m5 `make cloud-lock TYPE=3d-gpu` (resolve locally, show the diff). No
publish or promote target: only `main` deploys, and a local one would be a second
path around the federation condition.

Off the shelf instead: Renovate (as a GitHub Action) handles the base digest, GitHub
releases, Rust, Terraform and a custom source for Claude, with grouped pull requests
and auto-merge. It doesn't resolve an apt dependency closure or snapshot, or Homebrew
on Linux, so the resolver script has to exist anyway, and two bump systems feeding
one lock is worse than one (owner, 2026-11-06). Renovate does handle the GitHub
Actions, which are pinned by SHA ([design.md](design.md), the CI gate), and only
those (`enabledManagers` in `.github/renovate.json`).

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
| Claude Code | `CLAUDE_VERSION=2.1.317` (a version) or `stable` (the channel) | The watch then skips Claude. `nightly` types already run with `CLAUDE_UPGRADE=false`; a `weekly` type still updates itself at start, past the pin, and has no setting to stop it yet: add one (an env file key read by `gcp_runner_settings`) if a weekly pin is ever needed |
| Rust toolchain | `RUST_TOOLCHAIN=1.91.0` (a version) or `1.91` (a minor line) in `images/toolsets/rust/install.sh` | The watch resolves within it: a minor line still takes patch releases |
| Terraform | `TERRAFORM_VERSION=1.14.2` or `~> 1.14` in `images/toolsets/terraform/install.sh` | tenv takes the constraint as is |
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
  the execution ends, crash included. `lolay-agents` may give an instance up to
  200 GiB (`MaxPerInstanceEphemeralDiskSizePerProjectRegion` in `us-central1`, raised
  from the default 10 GiB on 2026-11-10), so every size above fits. All instances in
  the region share 500 GiB (`EphemeralDiskAllocPerProjectRegion`): a `gis` and a
  `3d-gpu` session at once plus a handful of `linux` ones. A session that would go
  past it doesn't start. `TMPDIR` and the caches (`XDG_CACHE_HOME`, pip, Homebrew)
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
the session or a full GPU desktop. Built in m2 (plan:
`specs/handoffs/plan-gcp-android-kestrel.md`, removed once it was done; in git history
at `0e34c0f`), deployed and tested 2026-11-06 ([m2 `android` results](#m2-android-results)).

- **Why it builds in GCP:** the other images are containers, built on the GitHub
  runner and pushed. This one is a Compute Engine disk image, and the build boots
  the emulator once for its quickboot snapshot, which needs KVM in the VM doing the
  build. Packer's `googlecompute` builder makes both easy: it boots a VM in the
  project, provisions it, and saves its disk as the image, in place. Building on the
  GitHub runner would mean producing a raw 100 GB disk there, then uploading and
  importing it: slower, more steps to fail, and a hosted runner with about 14 GB
  free to fit it on.
  The GitHub job only drives Packer and waits, so the build's cost is the build VM
  (an `n2-standard-8` for about 45 minutes, about $0.40) plus the smoke VM.
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
  `agent-gce-smoke` (every binary check and smoke test through
  `agent-toolset-checks`, plus sudo, KVM, scratch, and the emulator
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
  done 2026-11-06 ([m2 `android` results](#m2-android-results)).

#### m2 `gis` results

Done 2026-11-06: [lolay/agent-images#18](https://github.com/lolay/agent-images/pull/18)
(toolsets and `gis`), #19 (8 vCPU, 32 GiB) and #20 (the deploy's bake metadata).
[tests/sessions/gis.md](../tests/sessions/gis.md) passed every check that can pass,
with nothing installed during the session. What it found:

| Finding | Result |
| --- | --- |
| Stack | GDAL 3.13.3, PDAL 2.10.2, QGIS 4.2.3, PROJ 9.9.0; every binding imports. The 4 plugins the build drops (TileDB, MDAL) weren't needed |
| Cloud-native reads | NDVI for the box read 10.9 MB of 437 MB of band files (2.5%) over `/vsicurl/` |
| LiDAR | 8.97 M points from the 3DEP EPT for a 500 m square in 41 s, peak RSS under 1 GB |
| QGIS | `qgis_process` zonal statistics agree with the session's own to 5e-5; PyQGIS renders offscreen |
| Resources | 10 GiB scratch was enough; 32 GiB memory went unused by this test, so real workloads decide whether #19's size holds. `nproc` reports 9 for 8 vCPU |
| Not run | The cost line (no price lookup yet) and QGIS desktop (`gis` has no display) |
| Test fixes | The NDVI red-band offset and floor, noise before the DSM, inclusive `writers.gdal` bounds, a map inset, Overpass's encoding: folded into the test |

#### m2 `android` results

Done 2026-11-06: [lolay/agent-images#21](https://github.com/lolay/agent-images/pull/21)
(the type), then #23, #26, #28 and #30 for what the first builds found, deployed by
[run 18](https://github.com/lolay/agent-images/actions/runs/42162644618).
[tests/sessions/android.md](../tests/sessions/android.md) passed every check on an
"Agent: Android" session (2026-11-06T05:29Z), with nothing installed in the image during
the session.

**Recommendation: keep `android` on GCP**, with the Linux host's LXD image (same
scripts) as the fallback. Everything works end to end; the warm emulator boot beats
the Linux host's; builds and the Compose UI test ran without trouble; and a VM per
session needs no host to keep up. What GCP costs that the host doesn't: about $0.40 an
hour per session (`n2-standard-8`), a cold VM per session (spawn time not measured
yet), and a 27 s first emulator boot while the disk restores.

The session test:

| Check | Result |
| --- | --- |
| VM | Ubuntu 24.04.5, 8 vCPU, 31 GiB, 96 GB disk; passwordless sudo; `$DISPLAY` `:99`; `kvm-ok` passes |
| Toolchain | OpenJDK 21.0.12.1, platform `android-37.0`, build-tools 37.0.0, NDK 30.0.16248370, emulator 37.2.13.0 |
| Emulator | Booted before the session started (`agent-emulator wait` 0.25 s); API 37, x86_64 |
| App | Kotlin and Compose (AGP 9.5.1, Kotlin 2.4.21, Compose BOM 2026.09.00, Gradle 9.8.2): unit test, `assembleDebug` (11.1 MiB APK), Compose UI test on the emulator, install, two taps, screenshot: all pass, no `FATAL EXCEPTION` |
| Build times | Cold `assembleDebug` 54 s (dependencies already cached by the unit test), warm 19 s after a one-line edit; the Gradle wrapper's first fetch 57 s |
| Credentials | No unexpected credential names in the environment |
| Found in the test stack | Compose's `ui-test-junit4` pulls in `espresso-core` 3.5.0, which fails on API 37 (`InputManager.getInstance` is gone); pinning 3.7.0 fixes it. Now a line in the session's `CLAUDE.md` |
| Not on the VMs | `$AGENT_OUTPUTS` and `$AGENT_DATASETS` (open item below), and the session notice (cost, retire time), which only the Cloud Run hook sends |

What the deploys found:

| Finding | Result |
| --- | --- |
| Emulator boot from the snapshot, smoke VM (`n2-standard-8`) | 27.2 s on a fresh VM (its disk still restoring from the image), 6.1 to 6.4 s warm. The Linux host's hosted smoke: about 9 s |
| Snapshot boot in the Packer build VM | Works under nested KVM on Compute Engine: 43 to 45 s, saving the snapshot |
| Build time | About 43 min: provisioning 9 to 25 min (Google's regional Ubuntu mirror once served the JDK at 140 kB/s), then up to 30 min creating the 100 GB image (Packer's 5 min default timed out, #28) |
| Packer in CI | IAP SSH with no external IP works; provisioning has to run from `/` (#23), and the Homebrew installer's progress kept off stderr (#26) |
| Smoke on Cloud Run | Needed `TMPDIR` before the checks, since scratch is mounted empty (#30); that blocked `linux` and `gis` too, and with them the orchestrator |
| Smoke VM | `runuser -l` has no `/usr/sbin` on PATH; `kvm-ok` by its full path (#30) |
| Leftovers | A failed or cancelled Packer build can leave its build disk (and, when cancelled, its VM): delete `packer-*` disks and instances by hand. Don't cancel a build mid-run |

### m3 - GPU types

- Host to `e2-small`.
- Both GPU types install the `display` toolset ([image-toolsets.md](image-toolsets.md)
  s7), which already gives `linux` and `gis` a virtual display with software OpenGL;
  m3 adds GPU rendering on top of it.
- `linux-gpu` on an L4 first (cheaper), then RTX PRO 6000. Done when
  [tests/sessions/linux-gpu.md](../tests/sessions/linux-gpu.md) passes, which settles
  the open graphics question: if OpenGL and Vulkan don't run on the GPU in Cloud Run,
  GPU types move to the Compute Engine path (G4).
- `3d-gpu`: COLMAP, Blender and the splatting library built for compute capability
  12.0. Done when [tests/sessions/3d-gpu.md](../tests/sessions/3d-gpu.md) passes.
- Handoff: [handoffs/handoff-m3-gpu-types.md](handoffs/handoff-m3-gpu-types.md).

### m4 - Functional smoke and versions report

Two gaps the images have today, closed before the watch, since m5 depends on both. The
checks the entrypoint's `smoke` runs from `check.txt` only run `--version`: they
prove a binary is there, not that the toolchain builds anything, so a release that
breaks Go or Cargo promotes. And nothing records what an image contains:
`install-toolset.sh` logs each Brewfile's top-level formulae, not their dependencies,
and only in a layer that actually ran, so a cached layer leaves nothing; apt isn't
recorded at all; the deploy's summary holds only the digest. Independent of the
types and of m5.

**Binary checks and smoke tests (owner, 2026-11-06).**

- A **binary check** is a toolset's `check.txt` line, `label: command`: the tool is
  installed, on `PATH` and runs, and its first line of output is the version.
  Nothing more: no device, no display, no build. Every binary check runs wherever an
  image is checked.
- A **smoke test** is a toolset's `smoke.sh`: the least real work that shows the
  toolchain works (Go builds and runs hello; CUDA runs a kernel on the GPU). One per
  toolset at most, only where the binary check isn't enough.
- A smoke test runs in the pull request when that's cheap and easy on a GitHub
  hosted runner. One that needs what the runner lacks says so in its header,
  `# smoke: live (needs a GPU)`, and the pull request reports it as `skip`, never
  `pass`.
- Every smoke test can run live in the image: the deploy's smoke runs them all, on
  the type's own hardware, and a session runs any of them with
  `agent-toolset-checks smoke <name>`.
- The toolchains stay offline (no module, crate, package or provider fetches): the
  image has to work from what it holds. Fixture data too large for a heredoc is
  fetched at run time from a URL the test names, never baked into the image.

**The runner.** `images/toolsets/toolset-checks.sh`, installed in every runner image
(Cloud Run and android) as `/usr/local/bin/agent-toolset-checks`, is the one reader
of the record `install-toolset.sh` writes (`<name>.check`, and `smoke.sh` as
`<name>.smoke.sh`): `versions` runs the binary checks, `smoke-list` names the
toolsets with a smoke test, `live <name>` says whether one runs only live, and
`smoke <name>` runs one in a fresh temp directory under `$TMPDIR`, removed afterward,
in its own process group under `timeout 120`, with the toolchains' network off
(`GOPROXY=off`, `GOFLAGS=-mod=mod`, `GOTOOLCHAIN=local`, `CARGO_NET_OFFLINE=true`,
`UV_OFFLINE=1`, `UV_PYTHON_DOWNLOADS=never`, `npm_config_offline=true`,
`CHECKPOINT_DISABLE=1`). The entrypoint's `smoke` runs `claude`, the binary checks
(`binaries`), then each smoke test as its own check, in install order (`smoke: pass
smoke-go (go: built and ran hello)`, the detail being the test's last line), then
the mounts; `smoke pr` skips the live ones. The android smoke VM's `agent-gce-smoke`
runs the same binary checks and smoke tests; its own checks are only what the VM
adds (sudo, KVM, scratch, the emulator).

| Where | Binary checks | Smoke tests |
| --- | --- | --- |
| Pull request (`cloud-build.yml`, a GitHub hosted runner, no GCP access): each runner type loaded and run with `make cloud-smoke`, local volumes for the mounts | All | All but `live` ones |
| Deploy from `main`: `agent-<type>-next` in Cloud Run, the android smoke VM in Compute Engine | All | All |
| A session | `agent-toolset-checks versions` | `agent-toolset-checks smoke <name>` |

android isn't built on pull requests (Packer runs only in GCP), so its checks first
run on the deploy's smoke VM. The pull request check is what m5's lock pull
requests merge on.

**Versions report.** One script, `images/toolsets/write-versions.sh`, writes
`/etc/agent-images/versions.md`: the image type, build date and commit, Claude
Code's version, each binary check's label and version line (from
`agent-toolset-checks versions`), all of `brew list --versions` (dependencies and
`lolay/tap` included), `rustup toolchain list`, `tenv tf list`, `pixi list` for
`/opt/gis` on `gis`, and `dpkg-query -W -f '${Package} ${Version}\n'` inside a
collapsed `<details>`. A section whose tool isn't installed is left out.

- The cloud images write it in the last `RUN` of `images/cloud/runner/Dockerfile`,
  after Claude. It's the top layer, so it reruns whenever anything under it changed,
  and a cached copy is still accurate. `android` writes it at the end of
  `images/android/scripts/install-packages.sh`, the last of the installs in every
  android build (Packer, the LXD build, `android-image-smoke.yml`), with the
  checks' runner and the session skills, and prints it into the build log. Packer
  then downloads it from the build VM to `out/versions/android/versions.md`.
- It leaves the build without a pull from Artifact Registry: a `versions-<type>` bake
  target, `FROM scratch` with only that file copied from the type's image, built in
  the same `bake` call as the push with a `type=local` output, so it reuses the
  layers just built.
- `cloud-deploy.yml` appends it to `$GITHUB_STEP_SUMMARY` under the commit and the
  pushed digest, and uploads it as an artifact, `versions-<type>-<tag>.md`, with
  `retention-days: 90`; the `vm-images` job does the same for android's, under the
  image's name (`versions-<image name>.md`). `cloud-build.yml` does the same on pull requests, so a pull
  request shows what its image would contain.
- The entrypoint's `smoke` names the file, so the `-next` log and a session can
  find it.

Until m5 the record is the image itself (the registry keeps two per type) and 90 days
of Actions; from m5 the lock in git is the permanent one, and the report says what
each build actually got.

**The session skill.** `/agent-toolset-checks`
(`images/toolsets/skills/agent-toolset-checks/SKILL.md`), a Claude Code skill baked
into every runner image's `~/.claude/skills`, so it exists only in sessions on them. It asks
what to run (everything, binary checks, or chosen smoke tests), runs it through
`agent-toolset-checks`, reruns a failure once, and asks before filing an issue on
lolay/agent-images. Its `SKILL.md` is the one source for what such an issue holds:
the type, build date and commit from the versions report, what failed and the
command to rerun it, the last 50 lines of output, that toolset's lines from the
report, and nothing from the user's own work. Where the session's GitHub access
doesn't reach the repo, it prints a prefilled new-issue link instead.

**Smoke tests.** The least work that shows each toolchain runs, fixtures in heredocs.

| Toolset | Smoke test | Where |
| --- | --- | --- |
| native | C hello built with `cmake` and `ninja`, then run | Pull request |
| jvm | `javac` and `java` a Hello class | Pull request |
| node | `node` runs a script | Pull request |
| python | `uv venv` on the installed Python, a script run in it | Pull request |
| go | `go build` hello, run it | Pull request |
| rust | `cargo build` hello, run it | Pull request |
| proto | `protoc` a one-message file to a descriptor set | Pull request |
| terraform | `terraform init` and `validate` on a module with no providers | Pull request |
| display | The display answers, OpenGL renders on it, a screenshot of it is a PNG | Pull request |
| gis | `ogrinfo` reads a generated GeoJSON; the pixi Python imports its core modules, silently, on one GDAL | Pull request |
| gpu | `nvidia-smi`, `agent-cuda-probe` runs its kernel on the device, `agent-gpu-libs`; EGL and Vulkan reported, not failed | Live |
| android-go, android-rust | Cross-compile hello for arm64 with the NDK | Deploy smoke VM |

None for `core` or `browser` (binary checks are enough), or `android` (the smoke
VM's emulator boot is its smoke test). The display, gpu and gis checks that did work
(the screenshot, the device probe, the imports) moved out of `check.txt` into their
smoke tests, so every binary check passes on a hosted runner.

#### s1 - Binary checks and smoke tests `[deep]`

`toolset-checks.sh` and its tests; `install-toolset.sh` records `smoke.sh`; the
entrypoint and `agent-gce-smoke` run both through it; the display, gpu and gis
smoke tests, with their `check.txt` trimmed; `cloud-build.yml` loads and smokes
each runner target. Closes r1.f8 (`--task-timeout` on the smoke execution) and
most of r1.f27 (one runner for both images). The Makefile's `CLOUD_SCRIPTS`
already globs `images/toolsets/*.sh` and `images/toolsets/*/*.sh`, so shellcheck and
shfmt cover the new scripts unchanged.

#### s2 - Versions report `[deep]`

`write-versions.sh`, the runner Dockerfile's last layer, android's
`install-packages.sh` and Packer's download of the report, the `versions-<type>` bake
targets, and the summary and artifact steps in the deploy's `types` and `vm-images`
jobs and in the pull request check.

#### s3 - Session skill `[deep]`

`/agent-toolset-checks`, installed in both images, and a test that every runner
subcommand it names exists.

#### s4 - The other smoke tests `[exec]`

One subagent per toolset, on Sonnet, at most six at once: native, jvm, node,
python, go, rust; then proto, terraform, android-go, android-rust. Each gets this
contract in its prompt, verbatim:

- Write exactly one file, `images/toolsets/<name>/smoke.sh`, and touch nothing else.
  Fixtures go in heredocs inside it.
- `#!/bin/bash` and `set -euo pipefail`. It runs in the current directory, a fresh
  empty temp directory: no `cd` out of it, no writes elsewhere, no `sudo`.
- Offline: no registry, proxy, module, crate, provider or package fetches. Only the
  tools this toolset installs (read its `packages.txt`, `Brewfile`, `install.sh` and
  `check.txt`) and core's.
- The least real work that shows the toolchain runs: build or run one small thing.
  About 20 lines, under 30 s on a warm image.
- The last line of output is a one-line summary (`go: built and ran hello`).
- Passes `shellcheck -x` and `shfmt -d` with the repo's settings. If the toolset's
  tools are on the session's PATH, run it in a temp directory; otherwise say it
  wasn't run.
- Don't commit, push, or change branches. Report the file and what was verified.

The parent reviews each script against the contract, runs `make cloud-lint
cloud-test`, and commits each batch.

#### s5 - Verify on images `[deep]`

A pull request's `cloud-build` shows the binary checks, a `smoke-<name>` pass line
per pull request smoke test and a `skip` line for gpu for each type it loads, and a
versions report per type in its summary. A scratch commit that breaks one test
(`exit 1`) turns that check red and names the toolset. The first deploy from `main`
shows the same lines at `-next` in Cloud Run, and the next `android` deploy (or a
manual dispatch of it) shows the android binary checks and smoke tests on its smoke
VM. Results go under this milestone.

#### m4 results

Pull request lolay/agent-images#48, 2026-11-06.

- **The pull request check.** Every runner type is loaded and smoked on a hosted
  runner. After `--load`, 95 GB was free for `linux`, 89 GB for `gis` and 78 GB for
  `linux-gpu` (13.8 GB of images and 16.5 GB of build cache), so no type needs the
  no-output build. Job times: `linux` 9 min, `gis` 10.5 min, `linux-gpu` 14 min,
  `orchestrator` 2 min.
- **The smoke in the image** takes 7 s on `linux` (go's smoke test is the slowest,
  at 4.6 s) and 8 s on `gis`. Every binary check passes at build time and on the
  hosted runner. `linux-gpu` shows `skip smoke-gpu (live only: needs a GPU)`.
- **A failing smoke test turns the check red and names itself.** A scratch commit
  with `exit 1` in go's test turned `build (linux)` red with `smoke: FAIL smoke-go`.
  Every other check still ran and passed, and the versions report still uploaded.
- **The versions report** for `linux` is about 500 lines: 33 binary checks, 68
  Homebrew formulae with dependencies, and 370 apt packages. It's in each run's
  summary and kept as an artifact for 90 days.
- **The first run found two faults.** `make cloud-smoke` had never run in CI, and
  Docker refuses a read-only anonymous volume, so datasets is now a read-only tmpfs.
  `Xvfb -version` prints no version line, so its binary check is now `command -v`.
- **The first deploy from `main`** (run 42283134638, 36 min, green) showed the rest
  but one:
  - `-next` in Cloud Run: `linux`'s smoke execution (`agent-linux-next-qsbv7`)
    and `gis`'s passed, and both were promoted.
  - The android smoke VM ran every binary check and smoke test, android-go and
    android-rust for the first time (a cgo hello and a cargo-ndk cdylib for
    arm64), then booted the emulator in 27.3 s cold and 6.2 s warm (API 37,
    x86_64).
  - Each Cloud Run type's report is in the deploy summary and kept as an artifact.
  - Not yet shown: gpu's live smoke test, which waits on `linux-gpu`'s quota
    (`DEPLOY=false`) or a session on an L4.

### m5 - Dependency watch

The lock files (Homebrew recorded, never pinned; the base digest, the apt snapshot,
Claude, Rust and Terraform pinned), the hourly watch opening one auto-merging pull
request per run for `nightly` types, gated by m4's build and functional smoke, the
narrowed lock bot, the scheduled rebuilds building from the lock, the cleanup policy
out of dry run, and `make cloud-lock`; with the watch in place, turn off Claude's
self-update at start (`CLAUDE_UPGRADE=false`) on the `nightly` types only
([Rebuild cadence](#rebuild-cadence)). The schedules themselves already run. Needs m4.

- Handoff: [handoffs/handoff-m5-dependency-watch.md](handoffs/handoff-m5-dependency-watch.md).

#### m5 results so far

- **The first deploy from the locks** (run 42312876441, `main` at `10f4e44`, PR A):
  `gis` and `linux` built from their locks, passed `-next` in Cloud Run and were
  promoted; the android image built and passed its smoke VM as before. `linux-gpu`
  (`DEPLOY=false`) built and pushed from its lock. Every job green.
- **The watch's `resolve` on a hosted runner** (a throwaway job on PR B's branch,
  run 42314834276): every type resolved in about two minutes and matched its lock
  on `main`, so the apt snapshot held and the decision was "nothing to push". Two
  resolutions an hour apart agreeing is the stability the hourly watch needs.
- **PR B's deploy** (run 42425777979, `5bcaada`): `linux` and `gis` jobs rewritten
  with no image build (only `job-spec.sh` and `hooks/gcp` changed), `-next`
  passed, promoted; `linux`'s job now sets `CLAUDE_UPGRADE=false`. The
  orchestrator redeployed with the new `lib.sh`.
- **The loop, end to end, with no human** (2026-11-08). The first scheduled watch
  run (run 42431042335; the 16:17 Pacific slot, started 16:43) resolved `linux`,
  found Homebrew's `fmt` moved (12.2.0 to 12.2.1), and the bot opened #58,
  `lock(linux): fmt 12.2.1`, with auto-merge on. `cloud-build` built and smoked
  every type, `gate` passed, #58 merged itself (`27aa776`, the bot as
  co-author), and the push deploy (run 42432105264) built `linux` from the new
  lock, passed `-next` and promoted it. Plan's done criterion met, on a Homebrew
  move rather than a Claude release; the path is the same.
- **The next run** (run 42433885148; the 17:17 slot, started 17:21) resolved
  `linux` again, matched `main`, and skipped `propose`: nothing to push.
- **Schedules run in Pacific time.** Both runs landed in the Pacific slots, so
  `timezone: America/Los_Angeles` is honored. GitHub started them 4 to 26
  minutes late, so the hourly cadence is approximate.
- **The weekly run** (run 42482181775, Sunday 2026-11-08, 04:51 Pacific) resolved
  every type and opened #59, `lock(gis, linux-gpu): gis: fmt 12.2.1; linux-gpu:
  fmt 12.2.1`, which merged itself. So a `weekly` type's lock moves once a week,
  as designed.
- **Claude releases through the loop.** In the first three days the bot opened
  and merged seven lock pull requests (#58, #59, #60, #63, #64, #66, #69), all on
  green `gate`, none touched by a person. Two were Claude releases: #63
  (`claude 2.1.321, apt 2 packages`) and #64 (`claude 2.1.322`). That's the
  milestone's done criterion as written.
- **One failed run** (run 42673647532, 2026-11-09 10:23 Pacific): `resolve`
  passed and set its outputs, then the run failed without creating `propose`. The
  job logs don't say why; the run page's annotations should. The next hourly run
  proposed the same change (#63), so nothing was lost, but if it recurs it needs
  a root cause.
- Still to show: a pin with a closed issue in the summary; a red candidate
  refreshed rather than duplicated; a `linux` session showing
  `CLAUDE_UPGRADE=false` and no upgrade at start; the cleanup policy's first
  live run, once `cleanup_policy_dry_run = false` lands in lolay/infrastructure.

## Open items

| Item | Why |
| --- | --- |
| Graphics libraries on Cloud Run GPUs | Decides whether a virtual head runs on Cloud Run |
| Cold start of a multi-GB GPU image | Sets `--expected-spawn-seconds` and whether a standby is worth paying for |
| Cloud Run GPU quota grant | New projects get a default grant; may need a request for RTX PRO 6000 |
| `android` spawn time | Not measured: the session test starts once the VM is up. Time a queued session to its first prompt, then set `EXPECTED_SPAWN_SECONDS` (240 now) |
| Session notice on the `android` VMs | The cost and retire-time notice comes from the Cloud Run session hook; the VMs don't run it yet |
| Outputs and datasets on the `android` VMs | gcsfuse mounts like the Cloud Run types'; left out of m2, since its test doesn't use them |
| Serial port logging for the `android` VMs | Their journal reaches Cloud Logging only if `compute.disableSerialPortLogging` isn't enforced; m2's first deploy shows which |
| Data | Keep data-heavy work next to wherever its data is: cross-cloud egress is about $0.08 to $0.12 per GB |

## References

- [Claude Code on-demand runners](https://code.claude.com/docs/en/self-hosted-environments-configuration)
- [GCP network egress pricing](https://cloud.google.com/vpc/network-pricing)
- [EC2 Mac instances](https://aws.amazon.com/ec2/instance-types/mac/), [Tart on AWS Marketplace](https://tart.run/blog/2023/10/06/tart-is-now-available-on-aws-marketplace/), [Tart issue on headless mac2.metal](https://github.com/cirruslabs/tart/issues/1146)
- [Cloud Run GPU jobs](https://docs.cloud.google.com/run/docs/configuring/jobs/gpu), [execute jobs with overrides](https://docs.cloud.google.com/run/docs/execute/jobs), [worker pools](https://docs.cloud.google.com/run/docs/configuring/workerpools/gpu), [release notes](https://docs.cloud.google.com/run/docs/release-notes)
- [MIG standby pools](https://docs.cloud.google.com/compute/docs/instance-groups/suspended-and-stopped-vms-in-mig)
