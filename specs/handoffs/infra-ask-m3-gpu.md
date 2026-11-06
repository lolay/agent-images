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

3. **Ephemeral disk, later.** `linux-gpu`'s test fits the default 10 GiB. `3d-gpu`
   (120 renders, COLMAP's dense model, the splat model, PyTorch's caches) wants the
   planned 200 GiB per instance before s10, not now.

4. **Budget.** The project budget alerts at $50, $90 and $100 a month. Check the RTX
   PRO 6000 rate before s8: a day of GPU testing could hit those alerts.

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
