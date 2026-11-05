# Handoff: m2 `android` on Compute Engine (session)

Branch `claude/youthful-dijkstra-09v7kk` (assigned by the harness), from `main` at
`ecc54c2`. Plan: [plan-gcp-android-kestrel.md](plan-gcp-android-kestrel.md).

## Done

- Checked that lolay/agent-images#18 merged; cut the branch from the latest `main`.
- Read the brief's files and wrote the plan (s1 to s8). Nothing built yet.

## Pending: the owner's answer, verbatim

> Go on s1 to s6 (no GCP spend)? And the one departure from the brief: the reaper
> removes the work-order metadata key once the guest marks it read in a guest
> attribute, instead of the guest deleting it, because that would need
> `compute.instances.setMetadata` on `agent-runner`, which lets one session rewrite
> another session VM's startup-script.

## Resume

Read the plan, then start at s1 if the answer is go. s7 (the first deploy) waits for
a second go: it spends money, and needs the "Agent: Android" environment and
`claude-env-android` from the owner.

## Gotchas found while planning

- `images.patch` accepts `family`, so promotion is a patch, not an image copy.
- `maxRunDuration` on a standard VM requires `instanceTerminationAction`.
- Packer's `googlecompute` has `enable_nested_virtualization`, `use_iap`,
  `omit_external_ip`, `disable_default_service_account`; `use_iap` needs gcloud on the
  runner (setup-gcloud provides it).
- A disk restored from an image reads lazily, so the first emulator boot on a fresh VM
  may be much slower than the hosted runner's 9 s; the smoke VM times both boots.
