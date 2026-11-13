# Handoff: m3, the GPU types on GCP

Written 2026-11-06. The spec is [specs/gcp.md](../gcp.md) (m3 under
"Milestones"); m1's and m2's results are in its results tables.

## Where we are

Update 2026-11-13: `linux-gpu` runs on the RTX PRO 6000 (the L4 quota was denied),
is deployed, and passed its session test; Cloud Run renders on the GPU, so the
Compute Engine fallback isn't needed ([gcp.md, "m3 `linux-gpu` results"](../gcp.md#m3-linux-gpu-results)).
`3d-gpu` is next ([plan-gpu-types-ember.md](plan-gpu-types-ember.md), s9).

m1 (`linux`) and m2 (`gis`, `android`) are done and tested; m2 closed in
lolay/agent-images#35, which kept `android` on GCP (warm emulator boot 6 s, 27 s
from a disk still restoring, against the Linux host's 9 s). So the Compute Engine
path is proven, which matters for m3: it's the GPU types' fallback if Cloud Run
can't render. lolay/agent-images#36 added a rebuild cadence per type (`REBUILD`
in the env file); the GPU types are `weekly`.

What m3 builds on:

- **Images:** toolsets ([image-toolsets.md](../image-toolsets.md)), one layer
  each, then the shared runner stage (`images/cloud/runner`) adds the session files
  and Claude Code last. A type is two bake targets, `<type>-tools` and `<type>`, in
  `images/cloud/docker-bake.hcl`. The `display` toolset (Xvfb at `:99`, Mesa's
  llvmpipe, ImageMagick, xdotool) is already in `linux`, `gis` and `android`, and
  the entrypoint starts Xvfb before the runner and the smoke checks.
- **Jobs:** `scripts/cloud/job-spec.sh` renders a Cloud Run v2 job from
  `infra/gcp/<type>.env`. It has no GPU fields yet (no accelerator node selector,
  no `nvidia.com/gpu` limit, no zonal redundancy setting).
- **Deploy:** `cloud-deploy.yml` on `main` builds, smokes `agent-<type>-next` and
  promotes only what changed (`scripts/cloud/changed-targets.sh`). The smoke is the
  entrypoint's `smoke` mode, which has no GPU checks yet.
- **Compute Engine path:** built for `android` (Packer image, the hook's VM branch,
  the reaper). It's the GPU fallback if Cloud Run can't render on the GPU, so
  `android`'s numbers (cold start, boot from a lazily restored disk) are the first
  data on what that fallback costs.

## What m3 delivers

From gcp.md m3 and image-toolsets.md "Types":

- **Host to `e2-small`** (lolay/infrastructure: the machine type variable). Four
  orchestrators at about 85 MiB each don't fit the `e2-micro`'s free memory.
- **`agent-base-gpu`:** `images/cloud/base-gpu`, the same user and `core` toolset
  as `agent-base` on `mirror.gcr.io/nvidia/cuda:13.0.x-devel-ubuntu24.04`; a build
  parent only. Then GPU rendering on top of `display`: VirtualGL, `vulkan-tools`,
  `mesa-utils`, the GLVND and Vulkan ICD files that point at NVIDIA's libraries if
  the driver mount has them.
- **`linux-gpu`:** `linux`'s toolsets on `agent-base-gpu`, plus `cuda` and
  `display`. L4 first (cheaper, smaller minimums), then RTX PRO 6000 (20 vCPU, 80
  GiB minimum). Done when [tests/sessions/linux-gpu.md](../../tests/sessions/linux-gpu.md)
  passes. Step 4 of that test settles the open graphics question: if EGL and Vulkan
  don't reach the GPU on Cloud Run, the GPU types move to the Compute Engine path
  (G4) before `3d-gpu` is built.
- **`3d-gpu`:** `core`, `native`, `display` and a new `3d` toolset on
  `agent-base-gpu`: COLMAP built from source for compute capability 12.0 in a
  builder stage, Blender from blender.org, and a uv project at `/opt/3d` with
  PyTorch's CUDA build, gsplat (built at image time with
  `TORCH_CUDA_ARCH_LIST=12.0`), Open3D and ffmpeg. Done when
  [tests/sessions/3d-gpu.md](../../tests/sessions/3d-gpu.md) passes.
- For each type: `infra/gcp/<type>.env`, the bake targets, the smoke's GPU checks,
  the claude.ai environment ("Agent: Linux (GPU)", "Agent: 3D (GPU)") and the
  `claude-env-<type>` secret.

## Decisions already made

- `-gpu` is the CPU sibling plus the GPU: `linux-gpu` is `linux`'s toolset list on
  a CUDA base, not a new list (image-toolsets.md "Types").
- CUDA 13.0 userland, to match Cloud Run's mounted 580.x driver. Anything compiled
  targets `sm_120` or carries PTX. Newer CUDA means NVIDIA's forward-compatibility
  package, so don't move past 13.0 without it.
- CUDA's devel image, not runtime: sessions compile CUDA (the `linux-gpu` test
  does, with `nvcc`).
- `3d-gpu`'s Python is pinned to the newest version PyTorch, Open3D and gsplat all
  publish for (image-toolsets.md), not the newest CPython.
- `MIN_IDLE=0` for both: a standby GPU bills the whole time.
- No root on Cloud Run, as for every Cloud Run type: anything apt goes in the
  image.
- Region `us-central1`, the one with both L4 and RTX PRO 6000 on Cloud Run.
- The cost line and the m4 watch are still out of scope.

## Suggested plan

1. **Ask Gary for the infrastructure first, since it can lag:** in
   lolay/infrastructure, the host to `e2-small`, the Cloud Run GPU quota
   preference for L4 and RTX PRO 6000 in `us-central1` (gcp-infrastructure.md "GPU
   quota"; confirm the default grant first), and 100 GiB (`linux-gpu`) and 200 GiB
   (`3d-gpu`) of ephemeral disk per instance if those are wanted now rather than
   later. The claude.ai environments and secrets are Gary's too.
2. **`agent-base-gpu` and the GPU job spec:** the Dockerfile, its bake target,
   `job-spec.sh` taking an accelerator and GPU count from the env file, and GPU
   smoke checks (`nvidia-smi`, the driver mount, compute capability, EGL vendor,
   `vulkaninfo`) that report rather than fail where the answer is still open.
3. **`linux-gpu` on an L4:** env file, bake targets, deploy, session test. The test
   says RTX PRO 6000 and compute capability 12.0; on an L4 (8.9) read those
   checks as the L4's and note it. The graphics checks are what matter here.
4. **Decide Cloud Run or Compute Engine for GPU types** from step 4 of the test. If
   Compute Engine, plan the G4 path before going further: it reuses the `android`
   VM path, with a Packer image instead of a container.
5. **`linux-gpu` on RTX PRO 6000**, then the full session test.
6. **`3d-gpu`:** the `3d` toolset (COLMAP builder stage, Blender, `/opt/3d`), env
   file, deploy, session test.

## Gotchas

- **Hosted runner disk and cache:** about 14 GB free before the deploy's "Free
  disk" step, about 44 GB after it. `linux-gpu`'s tools are about 10.8 GB unpacked
  (CUDA devel 7.1 GB, 4.0 GB compressed; `linux`'s toolsets about 3.6 GB), so they
  fit (measured in m3.s5). The tighter limit is the Actions cache, 10 GB per repo:
  a GPU type's cache carries CUDA's 4 GB of layers. COLMAP's and gsplat's CUDA
  builds are also slow on 4 hosted vCPUs.
- **Building without a GPU:** the hosted runner has none, so `sm_120` code can be
  compiled there but not run. Nothing in the build should call a GPU; gsplat's
  build needs `TORCH_CUDA_ARCH_LIST` set because it can't detect one.
- **Smoke runs cost GPU time:** every deploy of a GPU type runs a smoke execution
  on a GPU job. Keep the smoke short, and make sure a failed smoke can't leave an
  execution running.
- **GPU job minimums:** RTX PRO 6000 jobs need at least 20 vCPU and 80 GiB; check
  the L4's minimums and the current field names for the accelerator and zonal
  redundancy in Cloud Run's docs before writing `job-spec.sh` (the GPU features
  were in preview when the spec was written; recheck).
- **Cold start:** the GPU images will be well past `linux`'s 5.5 GB (55 s cold
  start). Measure, and set `EXPECTED_SPAWN_SECONDS` from it, about three times the
  measured start as in m1.
- **Cloud Run quirks seen so far:** `nproc` reports more than the vCPU count; the
  hostname is `localhost`; explicit `uid`/`gid` GCS mount options got uid 2000.

## Files to read first

- [specs/gcp.md](../gcp.md): "Runner types", "Images", "The head", "Compute Engine
  VM per session", "Milestones", "Open items"
- [specs/image-toolsets.md](../image-toolsets.md): "Types", "`3d-gpu`: a Python
  pinned for the libraries", "Build wiring", "Plan" s7
- [specs/gcp-infrastructure.md](../gcp-infrastructure.md): "GPU quota", the host's
  machine type, the ephemeral disk quota
- `images/cloud/base/Dockerfile`, `images/cloud/linux/Dockerfile`,
  `images/cloud/docker-bake.hcl`, `images/cloud/entrypoint.sh` (smoke),
  `images/toolsets/display`
- `scripts/cloud/job-spec.sh`, `scripts/cloud/changed-targets.sh`,
  `.github/workflows/cloud-deploy.yml`
- [tests/sessions/linux-gpu.md](../../tests/sessions/linux-gpu.md),
  [tests/sessions/3d-gpu.md](../../tests/sessions/3d-gpu.md)

## Review

Pending: a full codebase review of `main` (`personal-codebase-review`), logged in
`specs/reviews/`, covering m1 and m2 together (Gary, 2026-11-05, in m2's handoff).
Gary chose to plan m3 first (2026-11-06); when the review lands, its high
findings in m1 or m2 code become steps in m3's plan.
