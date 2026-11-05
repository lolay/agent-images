# GCP infrastructure for agent-images

What `lolay/infrastructure` sets up in the `lolay-agents` GCP project for the runners
in [gcp.md](gcp.md), and what it leaves to this repo. Written 2026-11-05 as the brief
for the Terraform, and updated the same day to match what was applied
(`lolay/infrastructure`, `stacks/gcp/agents`). Terraform is the source of truth; this
page says what agent-images expects from it.

## What this is for

`lolay/agent-images` runs Claude Code self-hosted runners. Its GCP design: every
Claude Code cloud session gets a fresh Cloud Run job execution (CPU or an NVIDIA
RTX PRO 6000 GPU) built from images that agent-images builds and pushes from GitHub
Actions; the `android` type gets a Compute Engine VM per session instead, because it
needs KVM. One always-on `e2-micro` runs one Claude orchestrator per runner type, and
each orchestrator starts those executions or VMs.

The order of work: `linux` first and tested, then `gis` and `android`, then the GPU
types (`linux-gpu`, `3d-gpu`), at which point the orchestrator VM moves to
`e2-small`. Terraform for the first step needs everything below except the GPU
quota; the Compute Engine pieces are for `android`, the second step. Nothing from Agerpoint is involved: this
is a personal (`lolay`) project.

## The split

`lolay/infrastructure` (Terraform) owns the foundation: things that change about once.
`lolay/agent-images` owns the workloads: images, Cloud Run jobs, the orchestrator
container, and the list of runner types. Adding a runner type must not need a
Terraform change.

| Owned by Terraform here | Owned by agent-images |
| --- | --- |
| Project, APIs, budget | Images and their tags |
| Artifact Registry repo and cleanup policy | Cloud Run jobs, one per runner type (`agent-<type>`), created and updated by its deploy workflow |
| GCS bucket for order markers | The orchestrator container and its image digest |
| Network, Cloud NAT, the orchestrator VM, its boot config | The runner types (`linux`, `gis`, `android`, then `linux-gpu`, `3d-gpu`) |
| | The `android` Compute Engine image (Packer, image family `agent-android`) and the per-session VMs |
| Service accounts, IAM, workload identity federation | |
| GPU quota preference | |

Secret values never go through Terraform: Gary adds each runner type's Claude
environment key by hand (`gcloud secrets create claude-env-<type>`, then
`versions add`). Terraform grants access by name prefix instead, so a new type needs
no Terraform change and no key lands in state.

## What to create

### Project

- Project `lolay-agents`, linked to Gary's billing account. Under the lolay
  organization or folder if there is one (ask).
- Region `us-central1`, zone `us-central1-a` (or whichever zone has the free `e2-micro`
  and Cloud Run GPUs; the region is the constraint, not the zone).
- APIs: `run`, `artifactregistry`, `secretmanager`, `iam`, `iamcredentials`, `sts`,
  `compute`, `storage`, `logging`, `monitoring`, `cloudquotas`,
  `cloudresourcemanager`, `billingbudgets`, `cloudbilling` (enabled now for the cost
  notice, which reads current prices from the Cloud Billing Catalog API; the notice
  comes after m1, so nothing uses it yet).
- A billing budget on the project with alerts at 50%, 90%, 100% of $100 a month. GPU
  sessions are the cost risk.

### Artifact Registry

- Docker repo `agent-images` in `us-central1`.
- Cleanup policy: keep the two most recent versions per image, delete the rest and
  all untagged. (`google_artifact_registry_repository` `cleanup_policies` with a
  `most_recent_versions` keep of 2 and a delete on everything else.) Two, not one:
  after a push whose smoke test fails, the live digest is no longer the newest, and
  keeping one would delete it. Still a dry run (`cleanup_policy_dry_run = true`) until
  its log looks right.

### Storage

- Bucket for spawn-hook order markers, `lolay-agents-orders` (or with a suffix if
  taken): uniform access, no public access, lifecycle delete after 1 day, no
  versioning.
- Bucket `lolay-agents-outputs`: where sessions keep large results (point clouds,
  models, rasters, videos). Uniform access, no public access, lifecycle delete after
  30 days, no versioning. Sessions mount it read-write with Cloud Storage FUSE.
- Bucket `lolay-agents-datasets`: reusable inputs Gary adds by hand. Uniform access,
  no public access, no lifecycle rule. Sessions mount it read-only.
- Cloud Run ephemeral disk quota: 10 GiB per instance for every type for now, the
  default grant, which needs no approval. The region's 100 GiB default is granted when
  a job first uses scratch (until then the granted value reads 0, so Terraform doesn't
  manage the regional preference). `gis`, `linux-gpu` and `3d-gpu` need more (100, 100
  and 200 GiB per instance), asked for when those types arrive.

### Network and the orchestrator host

- A custom VPC `agents`, one subnet in `us-central1`. No ingress firewall rules at all
  (the default network's open SSH rule is the reason not to use it). Optional: allow
  IAP's range `35.235.240.0/20` on 22 for break-glass SSH.
- Cloud NAT on a Cloud Router in `us-central1` for outbound internet, built with the
  foundation. No VM gets an external IP: the orchestrator reaches Anthropic through
  NAT, and the `android` runner VMs come and go, so NAT is simpler than an address
  each.
- A firewall rule allowing IAP's range `35.235.240.0/20` on port 22 to instances
  tagged `packer` only: Packer reaches its build VM through IAP (`use_iap = true`),
  with no external IP.
- VM `agent-orchestrator`: `e2-micro` to start (GCP free tier in `us-central1`), Container-
  Optimized OS, service account `agent-orchestrator` (scope `cloud-platform`), shielded
  VM, OS Login on, no external IP, `google-logging-enabled`. Boot config: cloud-init
  that installs a systemd unit which
  1. long-polls metadata key `orchestrator-image` (`?wait_for_change=true`);
  2. accepts only a digest under the registry
     (`us-central1-docker.pkg.dev/lolay-agents/agent-images/<name>@sha256:...`); any
     other value is logged and ignored;
  3. when the value changes, pulls that image and runs it as container
     `agent-orchestrator` with `--restart=always` and `--stop-timeout 85`, keeping the
     old container if the pull fails. A value equal to the running image does
     nothing, so restarting the same image means resetting the VM.
  agent-images' deploy sets the key with
  `gcloud compute instances add-metadata agent-orchestrator --zone us-central1-a --metadata orchestrator-image=<digest ref>`;
  Terraform ignores changes to it
  (`lifecycle { ignore_changes = [metadata["orchestrator-image"]] }`). Until the first
  image exists, the unit waits quietly. The container's stdout goes to Cloud Logging;
  there is no SSH.
- Machine type as a variable: `e2-micro` now, `e2-small` when the GPU types arrive.
- Nested virtualization allowed: if the project sits under an organization, the
  `compute.disableNestedVirtualization` constraint must not be enforced for it. The
  `android` VMs turn it on per instance.

### Service accounts and IAM

The project is dedicated to this, so project-level grants are an acceptable scope.

| Service account | Grants |
| --- | --- |
| `agent-orchestrator` (the VM's) | `secretmanager.secretAccessor` with an IAM condition `resource.name.startsWith("projects/<number>/secrets/claude-env-")`; `run.developer` (execute jobs with overrides); `iam.serviceAccountUser` on `agent-runner` (jobs and runner VMs run as it); `storage.objectUser` on the marker bucket (create and read, and delete: the hook deletes its marker when a spawn fails); `artifactregistry.reader`; `logging.logWriter`; `monitoring.metricWriter`; for `android`, `compute.instanceAdmin.v1` (create runner VMs, and its reaper deletes stopped ones) and `compute.imageUser` |
| `agent-runner` (what job executions and runner VMs run as) | `logging.logWriter`; `storage.objectAdmin` on `lolay-agents-outputs` only; `storage.objectViewer` on `lolay-agents-datasets` only. Sessions get no secrets and no other GCP access |
| `agent-images-ci` (GitHub Actions, through federation) | `artifactregistry.writer` on the repo; `run.developer`; `iam.serviceAccountUser` on `agent-runner`; custom role `orchestratorDeployer` (`compute.instances.get`, `compute.instances.setMetadata`) on the orchestrator VM only; custom role `zoneOperationsReader` (`compute.zoneOperations.get`) at project level, since gcloud polls the operation `setMetadata` returns; `iam.serviceAccountUser` on `agent-orchestrator`, since `setMetadata` on a VM that runs as a service account needs actAs on it (it gives CI nothing new: CI already picks the image the orchestrator runs); for the `android` image, `compute.instanceAdmin.v1` and `compute.storageAdmin` (Packer's build VM and the image), `iap.tunnelResourceAccessor` (Packer's SSH) |

### Workload identity federation

- Pool `github`, provider `github-actions`, issuer `https://token.actions.githubusercontent.com`.
- Attribute mapping: `google.subject = assertion.sub`, plus `attribute.repository_id`,
  `attribute.repository_owner_id`, `attribute.ref`, `attribute.environment`.
- Attribute condition pinned to ids, not names (names can be re-registered):
  `assertion.repository_owner_id == "239275" && assertion.repository_id == "1394164208"
  && assertion.ref == "refs/heads/main" && assertion.environment == "production"`.
  Those are `lolay` and `lolay/agent-images`, checked 2026-11-05. The `ref` and
  `environment` clauses are the "only `main` deploys" rule: a pull request, another
  branch or a fork can't get a token at all.
- `agent-images-ci` grants `roles/iam.workloadIdentityUser` to
  `principalSet://iam.googleapis.com/projects/<number>/locations/global/workloadIdentityPools/github/attribute.repository_id/1394164208`.

### GPU quota

- A quota preference (`google_cloud_quotas_quota_preference`) for Cloud Run
  RTX PRO 6000 GPUs in `us-central1`, start at 1 or 2. New projects get a default
  grant on first deploy; confirm the default before asking for more. It may still need
  approval, so it can lag the apply.

## Outputs

agent-images keeps a committed copy of these in `infra/gcp/project.env` (its CI
configuration rule: non-secret values are committed, Terraform stays the source):

`PROJECT_ID`, `PROJECT_NUMBER`, `REGION`, `ZONE`, `ARTIFACT_REGISTRY`
(`us-central1-docker.pkg.dev/lolay-agents/agent-images`), `WIF_PROVIDER` (full
resource name), `CI_SERVICE_ACCOUNT`, `ORCHESTRATOR_SERVICE_ACCOUNT`,
`RUNNER_SERVICE_ACCOUNT`, `ORDER_BUCKET`, `OUTPUTS_BUCKET`, `DATASETS_BUCKET`,
`ORCHESTRATOR_INSTANCE`, `NETWORK`,
`SUBNET` (for the `android` VMs and Packer's build VM).

## Not in this repo (on purpose)

- Cloud Run jobs: agent-images creates and updates them, so a new runner type is one
  PR there.
- Secret values: by hand, never in state.
- The GitHub side (the lock-bot App, the `production` and `lock-bot` environments,
  the `main` ruleset): GitHub's own settings. The `integrations/github` provider could
  manage the environments and ruleset later; the App itself is created by hand.
- The `android` runner VMs and their image: agent-images' hook creates each VM from
  the `agent-android` image family with settings from its own `infra/gcp/android.env`,
  and its CI builds the image with Packer. Terraform supplies the network, NAT, IAP
  rule and permissions they use.

## Decided

1. The project sits under the lolay.com organization.
2. Terraform state lives in `lolay-infra-tf-state`, in the separate admin project
   `lolay-infra`, which also holds `lolay/infrastructure`'s own CI identities.
3. `lolay/infrastructure` plans on pull requests and applies on merge to `main`
   (`apply.yml`), through its own federation; `bootstrap/` is applied by hand.
4. The monthly budget is $100, alerts only.

## Gotchas

- Look up the latest stable Terraform and `hashicorp/google` provider versions before
  pinning (the `personal-new-project` skill); training data is stale.
- `principalSet` and conditions use the project number, not the id.
- Don't manage the job images or the VM's `orchestrator-image` metadata in Terraform,
  or every deploy shows as drift and the next apply rolls it back.
- The free `e2-micro` is one per billing account across the whole account, and only in
  `us-central1`, `us-west1` and `us-east1`.
- Cloud Run GPU jobs need the GPU quota and at least 20 vCPU and 80 GiB per RTX PRO
  6000 instance; agent-images sets those on the job, but the quota is here. Not
  needed until the GPU step.
- Nested virtualization needs an Intel machine series (`n2` to start) or AMD `n4d`;
  not `e2` or Arm. The default `n2` CPU quota in `us-central1` covers a few runner VMs
  at 8 vCPUs each.
