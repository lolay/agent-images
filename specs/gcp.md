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
Cloud Run worker pool  (one instance, no GPU, one per runner type)
  claude self-hosted-runner orchestrator --hooks-dir hooks/gcp
        │ spawn-runner: gcloud run jobs execute <type> --update-env-vars ...
        ▼
Cloud Run job execution  (one per session)
  agent-cloud-entrypoint → Xvfb (GPU types) → images/shared/runners/claude.sh
  registers with the work order, serves one session, exits → execution ends
```

| Today (Linux host) | GCP |
| --- | --- |
| `lxc launch agent-linux runner-N --ephemeral` | `gcloud run jobs execute agent-<type>`: one execution, gone when it exits |
| `build-image.sh` → `lxc publish agent-linux-next`, move the alias | Build and push to Artifact Registry, smoke-test `agent-<type>-next`, then point the job at the new digest |
| systemd user unit runs the orchestrator | Cloud Run worker pool, one instance |
| `~/.config/agent-images` holds the environment secret | Secret Manager, read only by the orchestrator's service account |
| `runner-once.sh` deletes the VM | Nothing to delete: the execution ends when the container exits |
| Claim `runner-1..N` with `mkdir` | Idempotency marker in GCS, created with `--if-generation-match=0` |

## Runner types

One Claude environment per type, each with its own orchestrator, job and image. The
environment is what you pick when starting a session, so it's the selector; types
never share capacity or images.

| Type | Image | Job resources | Standby |
| --- | --- | --- | --- |
| `cpu` | `agent-base` | 4 vCPU, 16 GiB | `--min-idle 1` (cheap, instant starts) |
| `gpu` | `agent-gpu` (from `agent-base`) | RTX PRO 6000, 20 vCPU, 80 GiB (the minimum for that GPU); or L4, 4 vCPU, 16 GiB, for cheap tests | `--min-idle 0`: a standby GPU bills the whole time |
| Later: `gis`, `3d` | From `agent-base` or `agent-gpu`, adding that domain's tools | As needed | As needed |

Region: `us-central1`, the one with both L4 and RTX PRO 6000 on Cloud Run.

## Images

We manage them, in this repo, next to the macOS and Linux images, and they share
`images/shared` so sessions behave the same everywhere.

```
images/cloud/
  base/Dockerfile      ubuntu:24.04; images/linux/packages.txt; Homebrew + Brewfile
                       as the runner user; Claude Code native installer;
                       images/shared (runner, watchdog, Stop hook, ~/.claude seed)
  gpu/Dockerfile       ARG BASE (CUDA 13.0 runtime on ubuntu:24.04), then the base
                       layers; Xvfb, VirtualGL, mesa-utils, vulkan-tools; optional noVNC
  <type>/Dockerfile    FROM agent-base or agent-gpu, adds the type's tools
  entrypoint.sh        start Xvfb when DISPLAY is wanted, then exec agent-runner
```

Choices, carried over from [design.md](design.md):

- **Track latest, pin on breakage.** No versions in package lists. The pipeline below
  rebuilds whenever anything upstream moves, so images stay current without a
  self-update on every start.
- **One unprivileged user with passwordless sudo**, like `admin` and `ubuntu`, not
  root: sessions can install what a repo needs, and nothing outlives the execution.
- **CUDA matches the host driver.** Cloud Run mounts its driver (580.x, CUDA 13.0) at
  `/usr/local/nvidia/lib64`, so `agent-gpu` uses a CUDA 13.0 userland. Newer CUDA
  needs NVIDIA's forward-compatibility package. Anything compiled (COLMAP, PyTorch
  extensions) must target compute capability 12.0 or carry PTX, or it fails on
  Blackwell.
- **No secrets in images or sessions** ([secrets.md](secrets.md)). The runner's
  service account gets logging only.

Layer order, slowest-moving first, so a rebuild only redoes what changed:

```
FROM <base>@<digest>      ubuntu:24.04, or nvidia/cuda 13.0 runtime for gpu
apt packages              packages.txt
Homebrew + Brewfile       incl. lolay/tap
type tools                gis, 3d, ...
images/shared files
Claude Code               ARG CLAUDE_VERSION; RUN install that exact version
manifest                  /etc/agent-images/manifest.json, also an OCI label
```

Claude goes last because it moves most often (most weekdays): a new release rebuilds
one layer.

## Image pipeline

GitHub Actions schedules and reports; Cloud Build does the work, in `us-central1` next
to the registry.

| Where the bytes go | GitHub builds | Cloud Build builds |
| --- | --- | --- |
| Push to Artifact Registry | Free (ingress) | Free (same region) |
| Pull the live image for the check, pull layer cache | GCP egress at $0.12/GB: a 3 GB image checked 15 times a day is about 1.35 TB, roughly $160 a month, more for a GPU image | Free (same region) |
| Compute | Free for a public repo | 2,500 free build-minutes a month on `e2-standard-2`, then $0.006/min |
| Disk | About 14 GB free on a standard runner: tight for a CUDA image | Set per build (`diskSizeGb`) |

So the Actions workflow is a thin job: authenticate through workload identity
federation (pinned to the repo's numeric id; fork pull requests get no OIDC token),
`gcloud builds submit` the check or the build, stream Cloud Build's log into the run
log, and write the manifest to the job summary. Logs stay on GitHub, bytes stay in
GCP. Expected use (a two-minute check 15 times a day, one or two builds) fits inside
the free build-minutes on `e2-standard-2`; GPU images may want a bigger machine type,
billed from the first minute.

### Watch: hourly, 08:00 to 22:00 Pacific

`cron: '17 8-22 * * *'` with `timezone: America/Los_Angeles` (schedules take a time
zone since March 2026). For each image, it compares what's promoted with what a build
would produce now:

| Dependency | What's promoted | What's current |
| --- | --- | --- |
| Base image | Digest in the manifest | `crane digest ubuntu:24.04` (or the CUDA tag) |
| Claude Code | `CLAUDE_VERSION` in the manifest | `https://downloads.claude.ai/claude-code-releases/latest` |
| apt packages | Run the promoted image: `apt-get update`, `apt list --upgradable` | Same command; Ubuntu's own answer |
| Homebrew, including `lolay/tap` | Run the promoted image: `brew update`, `brew outdated --json=v2` | Same command; Homebrew's own answer |
| Type tools from GitHub releases (COLMAP, ...) | Version in the manifest | The project's latest release |

Running the promoted image for apt and Homebrew means their own tools decide what's
outdated, taps included, with no version-lookup code of ours. It costs an image pull
per check, which is free inside Cloud Build and why the check doesn't run on GitHub.

State between runs: none in Actions. The registry is the state. The promoted image
carries its manifest as an OCI label, read with `crane config` without pulling, so the
check is stateless and can't drift from what's actually deployed.

### Rebuild when anything moved

Any difference starts a build. Layer caching (`--cache-from` the registry) keeps it to
the changed layer and the ones after it: minutes for Claude, longer for an apt
change. At most one build an hour per image, by the schedule; a `concurrency` group
per image stops overlap. Expect roughly one or two a day: Claude most weekdays, apt
security updates a few times a week, Homebrew less.

Every build follows the `-next` pattern from `build-image.sh`:

1. Push `us-central1-docker.pkg.dev/<project>/agent-images/<image>:<date>-<sha>`.
2. Point `agent-<type>-next` at that digest and run one smoke execution:
   `claude --version`, `nvidia-smi`, a CUDA device probe, `eglinfo` against Xvfb.
3. Only if it passes, point `agent-<type>` at the same digest. Cloud Run resolves the
   image when the job is updated, so running sessions never see a half-built image.

### Nightly: 04:00 Pacific, Tuesday through Saturday

A no-cache build with `--pull`, promoted the same way. It catches anything the watch
can't see (a curl installer, a changed package list in the repo) and keeps the
"track latest" promise even if a check is wrong. Pushes to `main` that touch
`images/cloud` or `images/shared` build too.

### Standbys

Only the CPU type keeps one (`--min-idle 1`), and it runs with `--exit-if-unused-min 60`,
so a standby is never more than an hour older than the newest image. GPU types keep
none. No recycling job needed.

### Keep one image, keep the logs

- **One image per type.** An Artifact Registry cleanup policy keeps the most recent
  version and deletes the rest, untagged layers included. Rollback is then a rebuild;
  keeping two would make it a job update.
- **Logs on GitHub.** Each build's run log, plus the manifest as an artifact and in the
  job summary: base digest, `dpkg-query -W`, `brew list --versions` and
  `--cask --versions`, `claude --version`, type tool versions, pins. Logs and
  artifacts keep 90 days (a public repo's maximum), and they're public, which is fine:
  builds hold no secrets. The same manifest is inside the image and on its label.

Make targets in the same vocabulary as the rest: `make cloud-build TYPE=gpu` (local
`docker build`), `make cloud-check` (the watch, locally), `make cloud-publish`,
`make cloud-promote`, `make cloud-status`.

## Pinning a dependency

Same rule as [design.md](design.md#track-the-latest-pin-only-on-breakage): pin only the
thing that broke, with a comment naming the upstream issue, and remove the pin once
it's fixed. How, per source:

| Dependency | Pin | Notes |
| --- | --- | --- |
| Base image | `FROM ubuntu:24.04@sha256:...` | The watch reports it as pinned instead of rebuilding |
| apt package | `name=version` in `packages.txt` | Ubuntu's `-updates` and `-security` pockets keep only the newest version, so an older one may vanish. To hold everything still, build with `apt-get --snapshot <timestamp>` against snapshot.ubuntu.com (apt 2.7+, in 24.04) |
| Homebrew formula | A versioned formula if one exists (`python@3.12`), else `brew extract --version=X <formula> lolay/tap` and name `lolay/tap/<formula>@X` in the Brewfile | `brew pin` doesn't help a fresh build. An extracted formula has no bottle, so it builds from source |
| `lolay/tap` formula | Revert in the tap, or add a versioned formula there | It's ours |
| Claude Code | `CLAUDE_VERSION=2.1.317` (a version) or `stable` (the channel) | The watch then skips Claude; set the runner's `CLAUDE_UPGRADE=false` so it doesn't update itself past the pin |
| Type tool from a release | Its version in the Dockerfile | |

Pins live where the dependency is declared, each with `# pinned: <issue URL>`. The
watch lists every pin in the job summary next to upstream's current version and the
issue's state, so a pin whose issue closed stands out instead of rotting.

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

## Setup

A short `scripts/gcp/setup.sh` (or OpenTofu once it outgrows a script) for one
project:

- Artifact Registry repo; GCS bucket for order markers.
- Service accounts: `agent-orchestrator` (Secret Manager accessor on its environment
  secret, `roles/run.developer` on its job for overrides, object create on the marker
  bucket); `agent-runner` (logging only); `agent-images-ci` (Artifact Registry writer,
  run.developer on the jobs, through workload identity federation).
- Per type: a Secret Manager secret with the environment key, the job, and the worker
  pool running the orchestrator image.
- GPU quota for Cloud Run in `us-central1`.

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
  LXD image, plus NVIDIA's driver on GPU types. A Compute Engine image instead of a
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

`agent-base` image, a Cloud Run job, an environment secret, and `claude
self-hosted-runner` started by a manual execution with the real secret. Proves the
image, the Stop hook and the git proxy in Cloud Run.

### m2 - GPU spike

`agent-gpu` on an L4 first (cheaper), then RTX PRO 6000: `nvidia-smi`, CUDA probe,
`eglinfo` and `vulkaninfo` against Xvfb, a Blender EEVEE render, a computer-use
screenshot of a GL app. Measure cold start. Decides Cloud Run or the fallback.

### m3 - Orchestrator and hook

`hooks/gcp/spawn-runner`, the orchestrator image, the worker pool, order markers. A
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
- [Cloud Build pricing](https://cloud.google.com/build/pricing), [network egress pricing](https://cloud.google.com/vpc/network-pricing)
- [EC2 Mac instances](https://aws.amazon.com/ec2/instance-types/mac/), [Tart on AWS Marketplace](https://tart.run/blog/2023/10/06/tart-is-now-available-on-aws-marketplace/), [Tart issue on headless mac2.metal](https://github.com/cirruslabs/tart/issues/1146)
- [Cloud Run GPU jobs](https://docs.cloud.google.com/run/docs/configuring/jobs/gpu), [execute jobs with overrides](https://docs.cloud.google.com/run/docs/execute/jobs), [worker pools](https://docs.cloud.google.com/run/docs/configuring/workerpools/gpu), [release notes](https://docs.cloud.google.com/run/docs/release-notes)
- [MIG standby pools](https://docs.cloud.google.com/compute/docs/instance-groups/suspended-and-stopped-vms-in-mig)
