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

- **Track latest, pin on breakage.** No versions in package lists; Claude Code updates
  itself at start (`claude.sh` already runs `claude update`). Images rebuild nightly.
- **One unprivileged user with passwordless sudo**, like `admin` and `ubuntu`, not
  root: sessions can install what a repo needs, and nothing outlives the execution.
- **CUDA matches the host driver.** Cloud Run mounts its driver (580.x, CUDA 13.0) at
  `/usr/local/nvidia/lib64`, so `agent-gpu` uses a CUDA 13.0 userland. Newer CUDA
  needs NVIDIA's forward-compatibility package. Anything compiled (COLMAP, PyTorch
  extensions) must target compute capability 12.0 or carry PTX, or it fails on
  Blackwell.
- **No secrets in images or sessions** ([secrets.md](secrets.md)). The runner's
  service account gets logging only.

Build and promote, the `-next` pattern from `build-image.sh`:

1. GitHub Actions builds each image on push and nightly, and pushes
   `us-central1-docker.pkg.dev/<project>/agent-images/<image>:<date>-<sha>`.
   Workload identity federation pinned to the repo's numeric id, no keys.
2. It updates `agent-<type>-next` to that digest and runs one smoke execution:
   `claude --version`, `nvidia-smi`, a CUDA device probe, `eglinfo` against Xvfb.
3. Only if that passes does it point `agent-<type>` at the same digest. Cloud Run
   resolves the image when the job is updated, so running sessions never see a
   half-built image, and rollback is pointing the job back at the old digest.

Make targets in the same vocabulary as the rest: `make cloud-build TYPE=gpu` (local
`docker build`), `make cloud-publish`, `make cloud-promote`, `make cloud-status`.

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
- **Android:** Compute Engine supports nested virtualization on Intel VMs, and with a
  VM per session the emulator is one level deep, not the two that ruled cloud VMs out
  in [linux.md](linux.md). Worth a test before buying the NUC.

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

GitHub Actions build, smoke, promote; nightly schedule; `make cloud-*` targets;
README version table rows.

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
- [Cloud Run GPU jobs](https://docs.cloud.google.com/run/docs/configuring/jobs/gpu), [execute jobs with overrides](https://docs.cloud.google.com/run/docs/execute/jobs), [worker pools](https://docs.cloud.google.com/run/docs/configuring/workerpools/gpu), [release notes](https://docs.cloud.google.com/run/docs/release-notes)
- [MIG standby pools](https://docs.cloud.google.com/compute/docs/instance-groups/suspended-and-stopped-vms-in-mig)
