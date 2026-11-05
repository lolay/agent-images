# Handoff: m2, `gis` and `android` on GCP

Written 2026-11-05 at the end of m1. The spec is [specs/gcp.md](../gcp.md) (m2 under
"Milestones"); m1's results are in its "m1 results" table.

## Where we are

m1 is done: the `linux` type runs on Cloud Run end to end and passed
[tests/sessions/linux.md](../../tests/sessions/linux.md) twice (the second on a fresh
runner). Landed in lolay/agent-images#13, #14, #15 and #16, plus
lolay/infrastructure#6 (`logging.viewer` for CI).

What m2 builds on:

- **Images:** `agent-base` (Ubuntu 24.04, `packages.txt`, the Brewfile toolchains,
  Claude Code last) and `agent-linux` (`FROM agent-base`), built with
  `images/cloud/docker-bake.hcl`. The entrypoint has a smoke mode the deploy runs on
  `agent-<type>-next`.
- **Jobs:** written through the Cloud Run v2 REST API by `scripts/cloud/job-spec.sh`
  from `infra/gcp/<type>.env`; scratch is `emptyDir` `medium: DISK`.
- **Orchestrator:** one container on the `e2-micro`, one orchestrator per
  `infra/gcp/*.env`. A new type gets one by adding its env file and secret.
- **Deploy:** `cloud-deploy.yml` on `main` builds, smokes and promotes only what
  changed (`scripts/cloud/changed-targets.sh`), and prints a failed smoke run's log.

Not done in m1, on purpose: the cost line (Billing Catalog), the m4 watch and lock.
Still worth doing: one more orchestrator memory reading after several idle hours, to
rule out a leak (m1 saw 78 then 84 MiB, five minutes apart, across a spawn):

```fish
gcloud logging read 'resource.type="gce_instance" AND "supervisor: memory"' --project lolay-agents --freshness 12h --order asc --limit 200 --format 'value(timestamp,textPayload,jsonPayload.message)'
```

## What m2 delivers

- `gis`: `images/cloud/gis/Dockerfile` (`FROM agent-base`, plus GDAL, PDAL, QGIS),
  `infra/gcp/gis.env`, the "Agent: GIS" environment and `claude-env-gis` secret.
  Done when [tests/sessions/gis.md](../../tests/sessions/gis.md) passes.
- `android`: the Compute Engine path in specs/gcp.md ("Compute Engine VM per
  session"): the Packer image (family `agent-android`), the hook's Compute Engine
  branch, the reaper in the orchestrator container. Done when
  [tests/sessions/android.md](../../tests/sessions/android.md) passes, with the
  emulator boot time compared against the Linux host's.

## Decisions already made

- No root on Cloud Run: Cloud Run sets no-new-privileges, so `sudo` and apt can't
  work. Everything `gis` needs from apt goes in its image at build time. Sessions
  install the rest with Homebrew. The `android` VMs keep passwordless sudo.
- No standby (`MIN_IDLE=0`): a held execution bills the whole time, and the `linux`
  cold start is about 55 s.
- The orchestrator host stays an `e2-micro` through m2 (about 85 MiB per
  orchestrator, about 610 MiB free with one). It moves to `e2-small` at m3.
- The hook and supervisor use REST with a metadata-server token, never gcloud on the
  host.

## Suggested plan

1. Run the codebase review of m1 on `main` (Review below), then plan m2 with tiers.
2. `gis` first: it reuses everything. Ask Gary for the scratch quota (100 GiB per
   instance, `ephemeral_disk_per_instance_gib` in lolay/infrastructure, which may need
   Google's approval), then the image, env file, secret, environment, and test.
3. `android`: Packer image from GitHub Actions, the hook's VM branch (create
   `agent-android-<order prefix>` with the work order in metadata), the reaper, the
   per-session guest flow (read and delete the metadata key, `EPHEMERAL=true`).

## Gotchas

- **Layer order:** a type's tools come after Claude Code in its image (`FROM
  agent-base`), so every Claude release rebuilds `gis`'s GDAL and QGIS layers. The
  spec puts type tools before Claude. Fix it when `gis` lands: build the type's tools
  on an `agent-base` stage that stops before Claude, then add Claude last.
- **Scratch quota:** `gis` wants 100 GiB, which is above the 10 GiB default and needs
  a quota request that may wait on Google. `job-spec.sh` takes `SCRATCH_GIB` from the
  env file.
- **Datasets file cache:** `gis` reads datasets repeatedly. The spec's FUSE file
  cache on `/scratch` isn't set yet (`job-spec.sh`'s datasets `mountOptions`).
- **Image size:** `agent-base` is about 5.5 GB since the toolchains (#15). `gis` adds
  QGIS's dependencies; watch the cold start, and the hosted runner's about 14 GB of
  free disk during builds.
- **Cloud Run quirks seen in m1:** `nproc` is 5 for a 4 vCPU job; the hostname is
  `localhost`; explicit `uid`/`gid` GCS mount options got uid 2000 (leave them out).
- **`android` is a different backend:** Compute Engine, not Cloud Run. It needs
  nested virtualization (`n2`), the subnet and NAT that lolay/infrastructure built,
  CI's Packer roles (already granted), and the orchestrator's
  `compute.instanceAdmin.v1` (already granted).

## Files to read first

- [specs/gcp.md](../gcp.md): "Runner types", "Storage", "Compute Engine VM per
  session", "Milestones", "m1 results"
- [specs/gcp-infrastructure.md](../gcp-infrastructure.md): what Terraform owns
  (quotas, android grants)
- `infra/gcp/linux.env`, `scripts/cloud/job-spec.sh`, `images/cloud/base/Dockerfile`,
  `images/cloud/docker-bake.hcl`
- `hooks/gcp/spawn-runner`, `images/cloud/orchestrator/supervisor.sh` (the reaper and
  the VM branch go here)
- [tests/sessions/gis.md](../../tests/sessions/gis.md),
  [tests/sessions/android.md](../../tests/sessions/android.md)

## Review

Pending, after m1's PRs merge: a full codebase review of `main`
(`personal-codebase-review`), logged in `specs/reviews/`, before m2 is planned.
