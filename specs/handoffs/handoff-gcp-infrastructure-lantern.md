# Handoff: Terraform for the `lolay-agents` GCP project

For a new session in `lolay/infrastructure`, which doesn't exist yet. It needs no
other context: the design it serves is `specs/gcp.md` in `lolay/agent-images`
(branch `claude/gifted-fermi-zgbs2z` until it merges), and that spec is worth reading
first. Written 2026-11-05.

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
  `cloudresourcemanager`, `billingbudgets`.
- A billing budget on the project with alerts at 50%, 90%, 100% of a monthly amount
  Gary picks. GPU sessions are the cost risk.

### Artifact Registry

- Docker repo `agent-images` in `us-central1`.
- Cleanup policy: keep the most recent version per image, delete the rest and all
  untagged. (`google_artifact_registry_repository` `cleanup_policies` with a
  `most_recent_versions` keep of 1 and a delete on everything else; run with
  `cleanup_policy_dry_run = true` first.)

### Storage

- Bucket for spawn-hook order markers, `lolay-agents-orders` (or with a suffix if
  taken): uniform access, no public access, lifecycle delete after 1 day, no
  versioning.

### Network and the orchestrator host

- A custom VPC `agents`, one subnet in `us-central1`. No ingress firewall rules at all
  (the default network's open SSH rule is the reason not to use it). Optional: allow
  IAP's range `35.235.240.0/20` on 22 for break-glass SSH.
- Cloud NAT on a Cloud Router in `us-central1` for outbound internet. No VM gets an
  external IP: the orchestrator doesn't need one, and the `android` runner VMs come
  and go, so NAT is simpler than an address each.
- A firewall rule allowing IAP's range `35.235.240.0/20` on port 22 to instances
  tagged `packer` only: Packer reaches its build VM through IAP (`use_iap = true`),
  with no external IP.
- VM `agent-orchestrator`: `e2-micro` to start (GCP free tier in `us-central1`), Container-
  Optimized OS, service account `agent-orchestrator`, shielded VM, OS Login on.
  Boot config: cloud-init that installs a systemd unit which
  1. reads metadata key `orchestrator-image` (an Artifact Registry digest),
  2. pulls and runs that container with `--restart=always`,
  3. long-polls the metadata server (`?wait_for_change=true`) and restarts the
     container when the key changes.
  agent-images' deploy sets that key; Terraform must ignore changes to it
  (`lifecycle { ignore_changes = [metadata["orchestrator-image"]] }`). Until the first
  image exists, the unit should wait quietly.
- Machine type as a variable: `e2-micro` now, `e2-small` when the GPU types arrive.
- Nested virtualization allowed: if the project sits under an organization, the
  `compute.disableNestedVirtualization` constraint must not be enforced for it. The
  `android` VMs turn it on per instance.

### Service accounts and IAM

The project is dedicated to this, so project-level grants are an acceptable scope.

| Service account | Grants |
| --- | --- |
| `agent-orchestrator` (the VM's) | `secretmanager.secretAccessor` with an IAM condition `resource.name.startsWith("projects/<number>/secrets/claude-env-")`; `run.developer` (execute jobs with overrides); `iam.serviceAccountUser` on `agent-runner` (jobs and runner VMs run as it); `storage.objectCreator` and `objectViewer` on the marker bucket; `artifactregistry.reader`; `logging.logWriter`; `monitoring.metricWriter`; for `android`, `compute.instanceAdmin.v1` (create runner VMs, and its reaper deletes stopped ones) and `compute.imageUser` |
| `agent-runner` (what job executions and runner VMs run as) | `logging.logWriter` only. Sessions get no secrets and no GCP access |
| `agent-images-ci` (GitHub Actions, through federation) | `artifactregistry.writer` on the repo; `run.developer`; `iam.serviceAccountUser` on `agent-runner`; `compute.instances.setMetadata` on the orchestrator VM only (a custom role, or `compute.instanceAdmin.v1` scoped to that instance); for the `android` image, `compute.instanceAdmin.v1` and `compute.storageAdmin` (Packer's build VM and the image), `iap.tunnelResourceAccessor` (Packer's SSH) |

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

agent-images keeps a committed copy of these in one file (its CI configuration rule:
non-secret values are committed, Terraform stays the source):

`PROJECT_ID`, `PROJECT_NUMBER`, `REGION`, `ZONE`, `ARTIFACT_REGISTRY`
(`us-central1-docker.pkg.dev/lolay-agents/agent-images`), `WIF_PROVIDER` (full
resource name), `CI_SERVICE_ACCOUNT`, `ORCHESTRATOR_SERVICE_ACCOUNT`,
`RUNNER_SERVICE_ACCOUNT`, `ORDER_BUCKET`, `ORCHESTRATOR_INSTANCE`, `NETWORK`,
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

## Open decisions for Gary

1. Does lolay have a GCP organization or folder for the project, or is it standalone?
2. Where Terraform state lives: a GCS bucket in a separate admin project (the usual
   answer, and where `lolay/infrastructure`'s own CI identity would live), or in
   `lolay-agents` itself.
3. How `lolay/infrastructure` applies: plan on pull request and apply on merge to
   `main` through its own federation, or apply by hand at first.
4. The monthly budget amount.

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

## Branch

This handoff lives on `claude/gifted-fermi-zgbs2z` in `lolay/agent-images`, a task
branch the harness assigned. Move it into `lolay/infrastructure` (or delete it here)
before that branch merges.
