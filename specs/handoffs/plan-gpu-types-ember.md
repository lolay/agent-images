# Plan: m3, the GPU types on GCP

Status: written 2026-11-06; no step started. Waiting on the owner's answers to the
decisions below.
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
  base-gpu    images/cloud/base/Dockerfile with BASE_IMAGE overridden: user, core,
              then the gpu toolset (CUDA env, GLVND/EGL and Vulkan ICD files for
              the driver Cloud Run mounts, VirtualGL, vulkan-tools)
    linux-gpu-tools   images/cloud/linux/Dockerfile, agent-base = base-gpu
      linux-gpu       the shared runner stage (Claude last)
    3d-gpu-tools      images/cloud/3d-gpu/Dockerfile: native, display, 3d
                      (COLMAP from a builder stage, Blender, /opt/3d)
      3d-gpu          the shared runner stage

infra/gcp/linux-gpu.env, 3d-gpu.env
  GPU_TYPE=nvidia-l4 | nvidia-rtx-pro-6000, GPU_COUNT=1, CPU, MEMORY
  -> job-spec.sh: accelerator node selector, nvidia.com/gpu limit,
     zonal redundancy off
```

## Decisions made here (say if you want otherwise)

- **One base Dockerfile, two bases.** `images/cloud/base/Dockerfile` takes
  `ARG BASE_IMAGE` (default Ubuntu through the mirror); bake target `base-gpu`
  overrides it with the CUDA devel image. `linux-gpu-tools` reuses
  `images/cloud/linux/Dockerfile` unchanged, with `agent-base` pointed at
  `base-gpu`. So `linux-gpu` really is `linux` plus the GPU, and can't drift from
  it. gcp.md's separate `images/cloud/base-gpu/Dockerfile` isn't needed.
- **A `gpu` toolset in `base-gpu` instead of `cuda` in `linux-gpu`.** Both GPU
  types need GPU rendering, and image-toolsets.md gives `3d-gpu` no `cuda` toolset.
  So the GPU pieces (CUDA's `PATH`, the GLVND EGL vendor file and the Vulkan ICD
  file pointing at NVIDIA's libraries, VirtualGL, `vulkan-tools`, `nvtop`) go in a
  `gpu` toolset that `base-gpu` installs after `core`. The CUDA toolkit itself comes
  with the base image. image-toolsets.md's tables change to match.
- **Build for the L4 and Blackwell both:** `sm_89` and `sm_120`, plus PTX for
  `compute_120`, everywhere we compile CUDA (COLMAP, gsplat, the smoke probe). It
  costs build time, and it lets the cheap L4 run the same image while we test.
- **GPU smoke checks report what they can't settle.** `nvidia-smi`, the driver mount
  and a CUDA device probe must pass. EGL and Vulkan on the GPU are the open
  question, so the smoke logs what they find and passes; the session test decides.
- **The test on an L4 counts for graphics, not for compute capability.** The
  linux-gpu test expects 12.0; on the L4 it reads 8.9, which is noted, not failed.

## Steps

#### s1 - Infrastructure asks [fast]

Draft the lolay/infrastructure change for Gary: `e2-small` for the host; Cloud Run
GPU quota preferences for L4 and RTX PRO 6000 in `us-central1` (confirm the default
grant first); ephemeral disk per instance for the GPU types if wanted now. Also
the two claude.ai environments and their secrets. Everything from s6 waits on this.

#### s2 - `base-gpu` and the `gpu` toolset [deep]

- `ARG BASE_IMAGE` in `images/cloud/base/Dockerfile`; bake target `base-gpu`.
- `images/toolsets/gpu`: `packages.txt` (VirtualGL from its own apt repo or
  release `.deb`, `vulkan-tools`, `libvulkan1`, `libglvnd`'s EGL pieces, `nvtop`),
  `install.sh` writing the EGL vendor JSON and Vulkan ICD JSON for the driver
  libraries under `/usr/local/nvidia/lib64` plus an `ld.so.conf.d` entry,
  `profile.sh` (CUDA on `PATH`), `CLAUDE.md`, `check.txt`.
- Build `base-gpu` locally or in CI without a GPU; nothing in it may need one.

#### s3 - GPU in the job spec [exec]

- `job-spec.sh`: `GPU_TYPE` and `GPU_COUNT` from the env file; when set, the
  accelerator node selector, the `nvidia.com/gpu` limit and zonal redundancy off.
  Field names and the L4's minimums from Cloud Run's current docs, not from memory.
- Its tests: a CPU type's spec is unchanged byte for byte; a GPU type's has the
  fields.

#### s4 - GPU smoke checks [exec]

The entrypoint's `smoke` mode, when `nvidia-smi` exists: GPU name, driver and
compute capability; the libraries under `/usr/local/nvidia/lib64`; a small CUDA
device probe (built into the `gpu` toolset at image time); `eglinfo -B` and
`vulkaninfo --summary` against Xvfb, logged as report-only.

#### s5 - `linux-gpu` type and its build [deep]

- `infra/gcp/linux-gpu.env` on the L4 (CPU and memory at the L4's minimums or the
  `linux` job's, whichever is larger), `MIN_IDLE=0`.
- Bake targets `base-gpu`, `linux-gpu-tools`, `linux-gpu`; `changed-targets.sh`
  knows `linux-gpu` depends on the `gpu` toolset and on everything `linux` does.
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
