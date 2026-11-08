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
     1 to 3, for s8 and `3d-gpu`.
   Until it lands, `linux-gpu.env` has `DEPLOY=false`: the image builds and
   pushes, and nothing else deploys. Remove that line, then run `cloud-deploy`
   with targets `linux-gpu orchestrator`.

3. **Ephemeral disk: 100 GiB per instance now, 200 GiB later.** `linux-gpu`'s test
   fits the default 10 GiB. `gis` and `linux-gpu` are planned at 100 GiB. `3d-gpu`
   (120 renders, COLMAP's dense model, the splat model, PyTorch's caches) wants 200
   GiB per instance, as two 100 GiB volumes, before s10; that's a new request when
   `3d-gpu` is built. The regional quota has to cover the peak too: 4 executions at
   100 GiB is 400 GiB in `us-central1`, against a 100 GiB default. Regional total
   approved at 500 GiB on 2026-11-08; per instance still 10 GiB (see the cases
   below).

4. **Budget.** The project budget alerts at $50, $90 and $100 a month. Check the RTX
   PRO 6000 rate before s8: a day of GPU testing could hit those alerts.

## Google Cloud Support cases

Two quota requests for `lolay-agents`. Support asked workload questions on
both; replies sent 2026-11-08. The case numbers aren't recorded here: they're in
Gary's email.

| Case | Quota | Asked for | Status |
| --- | --- | --- | --- |
| GPU | `run.googleapis.com/nvidia_l4_gpu_allocation_no_zonal_redundancy` and `run.googleapis.com/nvidia_rtx_pro_6000_gpu_allocation_no_zonal_redundancy`, `us-central1` | 3 of each | Waiting on Google |
| Ephemeral disk | Cloud Run ephemeral disk per instance, and the `us-central1` regional total | 100 GiB per instance (Support offered 100; accepted, since 200 is only for `3d-gpu`), 400 GiB regional | Regional 500 GiB approved 2026-11-08; per instance still 10 GiB, follow-up owed on the case |

Google's approval (Cloud Support, 2026-11-08) raised only the regional quota.
Checked 2026-11-08 with `gcloud beta quotas info list --project=lolay-agents
--service=run.googleapis.com --filter="metric~ephemeral"`:

| Quota ID | Metric | `us-central1` |
| --- | --- | --- |
| `EphemeralDiskAllocPerProjectRegion` | `run.googleapis.com/ephemeral_disk_allocation` | 536870912000 bytes (500 GiB) |
| `MaxPerInstanceEphemeralDiskSizePerProjectRegion` | `run.googleapis.com/max_per_instance_ephemeral_disk` | 10737418240 bytes (10 GiB, the default) |

To do: reply on the same case asking for
`MaxPerInstanceEphemeralDiskSizePerProjectRegion` at 100 GiB in `us-central1`.
Until it lands, keep every type's `SCRATCH_GIB` at 10 or below: a deploy asking for
more than the per-instance limit fails. Once it lands, update gcp-infrastructure.md
"Storage", which still says 10 GiB per instance for every type.

Cloud Run also requires disk per instance times maximum instances to fit the
regional total: 500 GiB covers 5 executions at 100 GiB, or two `3d-gpu` at 200.

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

### GPU case reply

> Hi,
>
> Thanks. Answers below.
>
> 1. Peak concurrency: 3 instances. Typically 1 or 2, and often none.
> 2. Ephemeral disk: 10 GiB per instance today. Later, 100 GiB on L4 instances and
>    200 GiB on RTX PRO 6000 instances. That's a separate case I have open.
> 3. Workload type: Cloud Run Jobs only. Each run is one job execution with one task.
> 4. Per instance: L4 jobs use 4 vCPU and 16 GiB. RTX PRO 6000 jobs use 20 vCPU and
>    80 GiB, the minimum for that GPU.
> 5. GPU: both L4 and RTX PRO 6000, one per instance, without zonal redundancy (jobs
>    require that). The quotas are `nvidia_l4_gpu_allocation_no_zonal_redundancy` and
>    `nvidia_rtx_pro_6000_gpu_allocation_no_zonal_redundancy` in us-central1. 3 of
>    each would cover us.
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

### Ephemeral disk case reply

> Hi,
>
> Thanks. Answers below.
>
> 1. Concurrency: up to 4 job executions at once, typically 1 or 2. Each execution
>    has one task.
> 2. Yes, Cloud Run Jobs only. No services.
> 3. 100 GiB per instance works for now. It covers the GIS and GPU job types. Since
>    each instance would use up to 100 GiB, I'd also need the us-central1 regional
>    ephemeral disk quota raised to 400 GiB to cover 4 at once. A photogrammetry type
>    later will want 200 GiB per instance, mounted as two 100 GiB volumes. I'll open
>    a new request when it's ready.
> 4. Per instance: GIS jobs use 8 vCPU and 32 GiB. GPU jobs use 4 vCPU and 16 GiB on
>    L4, or 20 vCPU and 80 GiB on RTX PRO 6000.
> 5. GPU: none for GIS. The GPU jobs use L4 and RTX PRO 6000, which I've requested in
>    a separate case.
> 6. Duration and frequency: each execution is one AI coding agent session that a
>    person starts, a few a day on weekdays, with no fixed schedule. Sessions
>    typically run 30 minutes to a few hours, with a hard timeout of 9.5 hours.
>    There's also a short weekly smoke test per image. The disk is scratch space for
>    downloads and intermediate rasters and point clouds, and it's discarded when the
>    execution ends.
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
