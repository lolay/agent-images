# m3 infrastructure ask (s1)

What m3 needs outside this repo, in the order it's needed. Checked against Cloud
Run's GPU docs on 2026-11-06
([jobs](https://docs.cloud.google.com/run/docs/configuring/jobs/gpu),
[quotas](https://docs.cloud.google.com/run/quotas)).

## lolay/infrastructure (`stacks/gcp/agents`)

1. **Orchestrator host to `e2-small`**, the machine type variable
   (gcp-infrastructure.md "Network and the orchestrator host"). Needed before the
   `claude-env-linux-gpu` secret exists: the supervisor starts a type's
   orchestrator only once its secret reads, and a fourth one on the `e2-micro`
   leaves about 270 MiB. About $12 a month.

2. **GPU quota: request it.** The docs' automatic grant on the first GPU deploy
   didn't happen: run 25 (2026-11-06) failed writing `agent-linux-gpu-next` with
   "You do not have quota for using GPUs without zonal redundancy", which jobs
   require. Request it at g.co/cloudrun/gpu-quota (IAM & Admin, Quotas & System
   Limits, service Cloud Run Admin API, `us-central1`), or through the quota
   preference in gcp-infrastructure.md "GPU quota":
   - `run.googleapis.com/nvidia_l4_gpu_allocation_no_zonal_redundancy`: 1 to 3,
     for s6.
   - `run.googleapis.com/nvidia_rtx_pro_6000_gpu_allocation_no_zonal_redundancy`:
     1 to 3, for s8 and `3d-gpu`. This one counts milli GPUs, so 3 GPUs is 3000
     (below).
   Until it lands, `linux-gpu.env` has `DEPLOY=false`: the image builds and
   pushes, and nothing else deploys. Remove that line, then run `cloud-deploy`
   with targets `linux-gpu orchestrator`.

3. **Ephemeral disk: done.** Google granted 200 GiB per instance (77257019,
   2026-11-10) and 500 GiB across `us-central1` (77257018, 2026-11-08), which
   covers `3d-gpu`'s 200 GiB too. specs/gcp.md "Storage" and gcp-infrastructure.md
   record both; `gis` runs at 100 GiB since lolay/agent-images#65.

4. **Budget.** The project budget alerts at $50, $90 and $100 a month. Check the RTX
   PRO 6000 rate before s8: a day of GPU testing could hit those alerts.

## Google Cloud Support cases

| Case | Quota (`us-central1`) | Status |
| --- | --- | --- |
| 77257018 | `EphemeralDiskAllocPerProjectRegion` | Granted 500 GiB, 2026-11-08 |
| 77257019 | `MaxPerInstanceEphemeralDiskSizePerProjectRegion` | Granted 200 GiB, 2026-11-10 |
| fad92fe0-b754-417c-aa73-f6a3f17d3e0c | `NvidiaRtxPro6000GpuAllocNoZonalRedundancyPerProjectRegion` 0 to 2000 | Granted 2000 (2 GPUs), 2026-11-13 |
| fad92fe0-b754-417c-aa73-f6a3f17d3e0c | `NvidiaL4GpuAllocNoZonalRedundancyPerProjectRegion` 0 to 2 | Denied, 2026-11-13 |

**GPU: RTX PRO 6000 granted, L4 denied (2026-11-13, same day as filed).** Google
partially approved the request: 2000 milli RTX PRO 6000, no L4. The denial gave the
stock reasons (wait 48 hours on a new project, more billing history, another region
or resource). The L4 was only m3's cheaper test GPU, so the plan is to skip it and
move `linux-gpu` to the RTX PRO 6000 (m3 s8) rather than ask again. Until the
request was filed, nothing had asked for GPU quota: the
console's Increase Requests tab held only 77257018 and 77257019 (filed 2026-11-04,
ephemeral disk only), and all four Cloud Run GPU quotas in `us-central1` read 0. The
reply drafted below was never sent. The eligibility check (`gcloud beta quotas info
describe ... --format="yaml(quotaIncreaseEligibility)"`) said `isEligible: true`
first. The request asks for 2 L4 and 2000 milli RTX PRO 6000 (Gary's call; Cloud Run
attaches whole GPUs only, so milli is just the unit), us-central1 only, peak 2 and
median 1. The console gave a case ID, not a number; the Increase Requests tab should
show numeric request numbers. `linux-gpu` stays `DEPLOY=false` until it moves to the RTX PRO 6000. In
the console, filter on "GPU": the display names say "without zonal redundancy", so
"no zonal redundancy" matches nothing.

The RTX PRO 6000 quota counts milli GPUs (its display name says "in milli GPU",
though `metricUnit` is `1`): 3 GPUs is 3000. The L4 quota counts whole GPUs. A
request for "3" RTX PRO 6000 would grant 0.003 of a GPU. gcp-infrastructure.md "GPU
quota" says "start at 1 or 2" without the unit; lolay/infrastructure's preference,
if it has one, needs the same check.

What the replies told Google, so a later request stays consistent:

- Cloud Run Jobs only, one task per execution, one execution per agent session,
  `MIN_IDLE=0`.
- Peak 3 GPU executions and 4 disk-using executions, typically 1 or 2. These are
  estimates: nothing in the repo caps concurrency.
- Sessions run minutes to a few hours, a few a day on weekdays, capped by
  `TASK_TIMEOUT_SEC` (9.5 hours), plus a short weekly smoke test per image.
- Per instance: `gis` 8 vCPU and 32 GiB; L4 4 vCPU and 16 GiB; RTX PRO 6000 20 vCPU
  and 80 GiB.
- Needed ongoing. No CE or FSR contacts.

### GPU case reply (draft, not confirmed sent)

> Hi,
>
> Thanks. Answers below.
>
> 1. Peak concurrency: 3 instances. Typically 1 or 2, and often none.
> 2. Ephemeral disk: already granted (200 GiB per instance, 500 GiB regional) in
>    cases 77257018 and 77257019.
> 3. Workload type: Cloud Run Jobs only. Each run is one job execution with one task.
> 4. Per instance: L4 jobs use 4 vCPU and 16 GiB. RTX PRO 6000 jobs use 20 vCPU and
>    80 GiB, the minimum for that GPU.
> 5. GPU: both L4 and RTX PRO 6000, one per instance, without zonal redundancy (jobs
>    require that). The quotas are `nvidia_l4_gpu_allocation_no_zonal_redundancy` and
>    `nvidia_rtx_pro_6000_gpu_allocation_no_zonal_redundancy` in us-central1. 3 of
>    each would cover us: 3 for L4, and 3000 for the RTX PRO 6000 quota, which
>    counts milli GPUs.
> 6. Usage pattern: intermittent bursts. Each execution backs one AI coding agent
>    session (ML and 3D reconstruction work) that a person starts. We keep no idle
>    instances (min 0), so GPUs are only allocated while a session runs. Sessions run
>    from minutes to a few hours, capped at 9.5 hours. There's also a weekly smoke
>    test of a few minutes per image.
> 7. Duration: ongoing.
> 8. No CE or FSR contacts.
>
> Thanks,
> Gary

## claude.ai and Secret Manager (by hand)

| Before | Environment | Secret |
| --- | --- | --- |
| s6 | "Agent: Linux (GPU)" | `claude-env-linux-gpu` |
| s10 | "Agent: 3D (GPU)" | `claude-env-3d-gpu` |

An env file can land before its secret exists: the supervisor retries a type whose
secret it can't read and starts the others anyway.

## Facts from the docs for later steps

- Minimums per instance: L4 4 vCPU and 16 GiB; RTX PRO 6000 20 vCPU and 80 GiB. One
  GPU per instance.
- Job fields (v1 YAML on the page; s3 finds the v2 REST names): node selector
  `run.googleapis.com/accelerator` = `nvidia-l4` or `nvidia-rtx-pro-6000`, limit
  `nvidia.com/gpu: 1`, annotation `run.googleapis.com/gpu-zonal-redundancy-disabled:
  'true'`.
- Driver 580.x (CUDA 13.0), libraries under `/usr/local/nvidia/lib64`, which Cloud
  Run appends to `LD_LIBRARY_PATH`.
- The pages don't say whether GPUs on jobs are GA or preview.
