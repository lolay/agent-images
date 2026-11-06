# Plan: m3, the GPU types on GCP

Status: s1 done 2026-11-06 ([infra-ask-m3-gpu.md](infra-ask-m3-gpu.md)). Layout
decided (owner, 2026-11-06). s2 to s4 done 2026-11-06; s5 in progress (env file
and bake target done; the deploy's cache waits on the owner).
Branch `claude/vigilant-tesla-m2ipbw` (assigned by the harness), from `main` at
`154787f`.

m3 in [specs/gcp.md](../gcp.md), with context in
[handoff-m3-gpu-types.md](handoff-m3-gpu-types.md). Done when
[tests/sessions/linux-gpu.md](../../tests/sessions/linux-gpu.md) and
[tests/sessions/3d-gpu.md](../../tests/sessions/3d-gpu.md) pass on an RTX PRO 6000.

**Review gate skipped (owner, 2026-11-06).** The full review of `main` covering m1
and m2 runs after this plan is written, not before. When it lands, add each of its
high findings in m1 or m2 code as a step here, before whichever step next touches
that code.

## Shape

```
mirror.gcr.io/nvidia/cuda:13.0.x-devel-ubuntu24.04
  base-gpu    images/cloud/base/Dockerfile with BASE_IMAGE overridden: user, core
    linux-tools-gpu   images/cloud/linux/Dockerfile, agent-base = base-gpu
      linux-gpu-tools images/cloud/linux-gpu/Dockerfile: FROM linux-tools-gpu,
                      then the gpu toolset (CUDA env, GLVND/EGL and Vulkan ICD
                      files for the driver Cloud Run mounts, VirtualGL,
                      vulkan-tools, a CUDA probe)
        linux-gpu     the shared runner stage (Claude last)
    3d-gpu-tools      images/cloud/3d-gpu/Dockerfile: native, display, 3d
                      (COLMAP from a builder stage, Blender, /opt/3d), then gpu
      3d-gpu          the shared runner stage

infra/gcp/linux-gpu.env, 3d-gpu.env
  GPU_TYPE=nvidia-l4 | nvidia-rtx-pro-6000, CPU, MEMORY
  -> job-spec.sh: accelerator node selector, nvidia.com/gpu limit,
     zonal redundancy off
```

## Decisions

- **One base Dockerfile, two bases (owner, 2026-11-06).**
  `images/cloud/base/Dockerfile` takes `ARG BASE_IMAGE` (default Ubuntu through the
  mirror); bake target `base-gpu` overrides it with the CUDA devel image.
  `linux-tools-gpu` reuses `images/cloud/linux/Dockerfile` unchanged, with
  `agent-base` pointed at `base-gpu`, so `linux-gpu` is `linux` plus the GPU and
  can't drift from it. gcp.md's separate `images/cloud/base-gpu/Dockerfile` isn't
  needed. If `linux-gpu` ever has to differ from `linux`, it gets its own copy then.
- **A `gpu` toolset on top of each GPU type (owner, 2026-11-06).** The GPU pieces
  (CUDA's `PATH`, the GLVND EGL vendor file and the Vulkan ICD file pointing at
  NVIDIA's libraries, VirtualGL, `vulkan-tools`, `nvtop`, a small CUDA probe) are
  the `gpu` toolset, the last tools layer of `linux-gpu` and `3d-gpu`. On top
  rather than in `base-gpu`, because s2 to s7 iterate on those files and a change
  low in the stack would rebuild every toolset above it. Each GPU type gets a small
  `images/cloud/<type>/Dockerfile`, which also gives `changed-targets.sh` a
  directory to map. Once s7 settles graphics and the files stop moving, moving it
  into `base-gpu` is optional. It replaces image-toolsets.md's `cuda` toolset (the
  CUDA toolkit comes with the base image); its tables change to match.
- **Build for the L4 and Blackwell both (owner, 2026-11-06):** `sm_89` and `sm_120`, plus PTX for
  `compute_120`, everywhere we compile CUDA (COLMAP, gsplat, the smoke probe). It
  costs build time, and it lets the cheap L4 run the same image while we test.
- **GPU smoke checks report what they can't settle.** `nvidia-smi`, the driver mount
  and a CUDA device probe must pass. EGL and Vulkan on the GPU are the open
  question, so the smoke logs what they find and passes; the session test decides.
- **The test on an L4 counts for graphics, not for compute capability.** The
  linux-gpu test expects 12.0; on the L4 it reads 8.9, which is noted, not failed.

## Steps

#### s1 - Infrastructure asks [fast] (done)

Draft the lolay/infrastructure change for Gary: `e2-small` for the host; Cloud Run
GPU quota preferences for L4 and RTX PRO 6000 in `us-central1` (confirm the default
grant first); ephemeral disk per instance for the GPU types if wanted now. Also
the two claude.ai environments and their secrets. Everything from s6 waits on this.
Written to [infra-ask-m3-gpu.md](infra-ask-m3-gpu.md): the first GPU deploy gets 3 L4
and 3 RTX PRO 6000 automatically, so the quota likely needs no Terraform.

#### s2 - `base-gpu` and the `gpu` toolset [deep] (done)

- `ARG BASE_IMAGE` in `images/cloud/base/Dockerfile`; bake target `base-gpu`.
  The CUDA image is built on `ubuntu:24.04`, so its `ubuntu` user (uid 1000)
  should be there; check rather than assume.
- `images/toolsets/gpu`: `packages.txt` (VirtualGL from its own apt repo or
  release `.deb`, `vulkan-tools`, `libvulkan1`, `libglvnd`'s EGL pieces, `nvtop`),
  `install.sh` writing the EGL vendor JSON and Vulkan ICD JSON for the driver
  libraries under `/usr/local/nvidia/lib64` plus an `ld.so.conf.d` entry,
  `profile.sh` (CUDA on `PATH`), `CLAUDE.md`, `check.txt`.
- `images/cloud/linux-gpu/Dockerfile` (`FROM` `linux-tools-gpu`, then `gpu`) and
  its bake targets, so s2 builds a whole `linux-gpu-tools` image.
- Build it locally or in CI without a GPU; nothing in it may need one.

Done: built `linux-gpu-tools` locally on CUDA 13.0.3 devel and checked it. The
CUDA image has the `ubuntu` user (uid 1000); nvcc 13.0; `agent-cuda-probe` carries
sm_89 and sm_120 SASS and sm_120 PTX, and without a GPU fails cleanly ("driver
version is insufficient"); VirtualGL 3.1.5 (`/usr/bin/vglrun`); both vendor files;
the report-only checks name llvmpipe for EGL and Vulkan without a GPU. Found on
the way:

- CUDA's image sets `NVIDIA_DRIVER_CAPABILITIES=compute,utility`, which under
  NVIDIA's container toolkit leaves out the graphics libraries; the `linux-gpu`
  Dockerfile sets `all`. `3d-gpu`'s Dockerfile needs the same line (s10).
- CUDA's image has its own `ENTRYPOINT` (a banner script); the runner stage's
  replaces it, so nothing to do.
- Size: Docker reports 16.3 GB locally for `linux-gpu-tools`, unpacked. s5's
  hosted-runner disk question is real.
- Building locally in a Claude cloud session needs its TLS proxy's CA in the
  base (a scratch image, passed with `--set base-gpu.args.BASE_IMAGE=...`); CI
  doesn't. The `core` toolset then fails late and unclearly: Homebrew's
  installer's curl error doesn't stop `install-toolset.sh`, which dies on the
  missing `brew` (a pre-existing gap, not fixed here).

#### s3 - GPU in the job spec [exec] (done)

- `job-spec.sh`: `GPU_TYPE` and `GPU_COUNT` from the env file; when set, the
  accelerator node selector, the `nvidia.com/gpu` limit and zonal redundancy off.
  Field names and the L4's minimums from Cloud Run's current docs, not from memory.
- Its tests: a CPU type's spec is unchanged byte for byte; a GPU type's has the
  fields.

Done: `GPU_TYPE` alone (no `GPU_COUNT`: Cloud Run takes only one GPU per
instance) adds `nodeSelector.accelerator`, `gpuZonalRedundancyDisabled: true`
and `nvidia.com/gpu: "1"`, the v2 REST names from Cloud Run's API reference.
An unknown `GPU_TYPE` exits 2 (the docs warn that `nvidia-l4` is easy to mistype).
`linux` and `gis` render byte for byte as before; three new tests; the full unit
suite passes.

#### s4 - GPU smoke checks [exec] (done)

The entrypoint's `smoke` mode, when `nvidia-smi` exists: GPU name, driver and
compute capability; the libraries under `/usr/local/nvidia/lib64`; a small CUDA
device probe (built into the `gpu` toolset at image time); `eglinfo -B` and
`vulkaninfo --summary` against Xvfb, logged as report-only. Mostly done in s2:
the `gpu` toolset's `check.txt` has all of these, and the entrypoint already runs
them. What's left is checking the driver mount's contents (which graphics
libraries Cloud Run actually mounts), the most useful fact for s7.

Done: `agent-gpu-libs` (in the `gpu` toolset, a report-only smoke check) prints
the driver version and which library groups the host mounted: compute, EGL, GLX,
Vulkan, NVENC, NVDEC, OptiX, naming what's missing. So the first deploy's smoke
log answers whether Cloud Run gives graphics libraries at all, before any session
runs. Unit tests in `tests/unit/test_gpu_libs.py`; checked in the rebuilt image.

#### s5 - `linux-gpu` type and its build [deep]

- `infra/gcp/linux-gpu.env` on the L4 (CPU and memory at the L4's minimums or the
  `linux` job's, whichever is larger), `MIN_IDLE=0`.
- Bake target `linux-gpu` (the runner stage) and the deploy's target list;
  `changed-targets.sh` follows `linux-gpu`'s Dockerfile chain through
  `linux-tools-gpu` to `linux`'s toolsets and `base`.
- The deploy's disk: measure the image against the hosted runner's free disk
  (about 14 GB after "Free disk"); if it doesn't fit, a larger runner or
  pruning between stages, decided here.
- Docs: gcp.md "Images" and "Runner types", image-toolsets.md "Types".

This and s2 to s4 are one pull request; its merge deploys `linux-gpu` to the L4.

#### s6 - First deploy and the session test on an L4 [deep]

Watch the deploy (build time, image size, GPU smoke). Set `EXPECTED_SPAWN_SECONDS`
from the cold start. Run the linux-gpu session test in "Agent: Linux (GPU)";
record the results in gcp.md as "m3 `linux-gpu` results".

#### s7 - Cloud Run or Compute Engine for GPU types [deep]

From s6's step 4 (EGL, Vulkan, VirtualGL on the GPU): write the decision for
Gary. If Cloud Run renders on the GPU, carry on. If not, stop: the GPU types move
to the Compute Engine path (G4), which is a new plan built on the `android` VM
path, and s8 to s10 change.

#### s8 - `linux-gpu` on RTX PRO 6000 [exec]

Switch the env file to RTX PRO 6000 (20 vCPU, 80 GiB), deploy, rerun the session
test; add the numbers to the results table.

#### s9 - The `3d` toolset [deep]

- A builder stage for COLMAP from source with
  `CMAKE_CUDA_ARCHITECTURES="89;120"`, copying only its install tree.
- Blender from blender.org's Linux build (its own Python and Cycles kernels).
- `/opt/3d`: a uv project with `requires-python` at the newest version PyTorch,
  Open3D and gsplat all publish for; PyTorch from its CUDA 13.0 index; gsplat
  built with `TORCH_CUDA_ARCH_LIST="8.9;12.0+PTX"`; ffmpeg.
- `CLAUDE.md` and `check.txt` (including `cuobjdump --list-elf` showing `sm_120`).

#### s10 - `3d-gpu` type, deploy and session test [deep]

`images/cloud/3d-gpu/Dockerfile`, bake targets, `infra/gcp/3d-gpu.env` on RTX PRO
6000, deploy, the 3d-gpu session test, "m3 `3d-gpu` results" in gcp.md. s9 and s10
are one pull request.

#### s11 - Close m3 [exec]

gcp.md's status line and open items (graphics on Cloud Run GPUs, cold start of a
multi-GB GPU image, GPU quota); remove this plan and its handoff, or promote what's
durable; write the m4 handoff.
