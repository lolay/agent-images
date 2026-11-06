# m3 infrastructure ask (s1)

What m3 needs outside this repo, in the order it's needed. Checked against Cloud
Run's GPU docs on 2026-11-06
([jobs](https://docs.cloud.google.com/run/docs/configuring/jobs/gpu),
[quotas](https://docs.cloud.google.com/run/quotas)).

## lolay/infrastructure (`stacks/gcp/agents`)

1. **Orchestrator host to `e2-small`**, the machine type variable
   (gcp-infrastructure.md "Network and the orchestrator host"). Needed before
   `linux-gpu`'s env file lands on `main`, since that adds a fourth orchestrator to
   an `e2-micro` with about 270 MiB to spare. About $12 a month.

2. **GPU quota: probably nothing to apply.** The first GPU deploy in a region gets
   an automatic grant, subject to capacity: 3 L4 GPUs and 3,000 milliGPU (3) RTX PRO
   6000, both with zonal redundancy off, which jobs require. That covers m3. So:
   - If the `google_cloud_quotas_quota_preference` from gcp-infrastructure.md "GPU
     quota" isn't applied yet, leave it out. If it is, check that its value isn't
     below what was granted, since a preference can lower a quota as well as raise
     it.
   - After s6's first deploy, confirm the grant in the console (IAM & Admin, Quotas
     & System Limits, service Cloud Run Admin API). Metrics:
     `run.googleapis.com/nvidia_l4_gpu_allocation_no_zonal_redundancy` and
     `run.googleapis.com/nvidia_rtx_pro_6000_gpu_allocation_no_zonal_redundancy`.
   - gcp-infrastructure.md "GPU quota" changes to say this.

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
