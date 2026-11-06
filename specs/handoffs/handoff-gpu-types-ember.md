# Handoff: m3 GPU types (session)

Branch `claude/vigilant-tesla-m2ipbw` (assigned by the harness, a task branch),
from `main` at `154787f`. Plan: [plan-gpu-types-ember.md](plan-gpu-types-ember.md).

## Done

- The m3 milestone handoff, [handoff-m3-gpu-types.md](handoff-m3-gpu-types.md),
  linked from gcp.md's m3.
- The tiered m3 plan, s1 to s11; s1 (the infrastructure ask) done.
- CUDA builds target `sm_89` and `sm_120` plus PTX (owner).

## Pending

Layout decided (owner): one base Dockerfile with `BASE_IMAGE`, `linux-gpu` reusing
`linux`'s Dockerfile, and a `gpu` toolset on top of each GPU type.

s2 in progress: the files are committed; the local build of `linux-gpu-tools`
(`docker buildx bake -f images/cloud/docker-bake.hcl linux-gpu-tools`) was running
and hasn't been checked. On resume, rerun it, then check in the image: the `ubuntu`
user, `nvcc`, `agent-cuda-probe` (`cuobjdump --list-elf` shows sm_89 and sm_120),
`vglrun`, `eglinfo`, `vulkaninfo`, and the two vendor JSON files.
The owner acts on [infra-ask-m3-gpu.md](infra-ask-m3-gpu.md).

The owner chose to skip the review gate: the full review of `main` covering m1 and
m2 runs later, and its high findings become steps in this plan.

m2 isn't closed: `android`'s session test was running elsewhere when this was
written.

## Resume

Read the plan, check its Status line, start at the first step not marked done.
