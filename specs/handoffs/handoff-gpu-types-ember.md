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

s1 to s5 done (see the plan's notes), in lolay/agent-images#37, which this
session watches for CI and reviews. The owner merges it, after `e2-small` for the
host and the "Agent: Linux (GPU)" environment with `claude-env-linux-gpu`
([infra-ask-m3-gpu.md](infra-ask-m3-gpu.md)). The merge deploys `linux-gpu` to the
L4 and starts s6: read the smoke's `driver-libs` line first.

The owner chose to skip the review gate: the full review of `main` covering m1 and
m2 runs later, and its high findings become steps in this plan.

m2 closed on `main` (lolay/agent-images#35), and the branch has `main` merged in,
with #36's rebuild cadence (`linux-gpu` is `weekly`).

## Resume

Read the plan, check its Status line, start at the first step not marked done.
