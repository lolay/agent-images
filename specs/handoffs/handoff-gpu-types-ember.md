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

s2 to s4 done (see the plan's notes). s5 in progress: `infra/gcp/linux-gpu.env`
(L4) and the `linux-gpu` bake target are committed; `changed-targets.sh` lists
`linux-gpu` with no change. Disk turned out fine (about 44 GB free after "Free
disk", the image about 10.8 GB unpacked). Pending question to the owner: the
deploy's Actions cache for the GPU parents (`base-gpu`, `linux-tools-gpu`) when the
repo's cache is 10 GB and CUDA alone is 4 GB of it. Options asked: cache them like
the other types and accept eviction; cache nothing for GPU parents; or raise the
repo's cache limit.

The owner chose to skip the review gate: the full review of `main` covering m1 and
m2 runs later, and its high findings become steps in this plan.

m2 isn't closed: `android`'s session test was running elsewhere when this was
written.

## Resume

Read the plan, check its Status line, start at the first step not marked done.
