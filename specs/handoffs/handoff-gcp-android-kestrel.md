# Handoff: m2 `android` on Compute Engine (session)

Branch `claude/youthful-dijkstra-09v7kk` (assigned by the harness), from `main` at
`ecc54c2`. Plan: [plan-gcp-android-kestrel.md](plan-gcp-android-kestrel.md).

## Done

- Checked that lolay/agent-images#18 merged; cut the branch from the latest `main`.
- Wrote the plan (s1 to s8); built s1 to s6 (commits m2.s1 to m2.s6), and the PR draft `plan-gcp-android-kestrel-review.md`.

## Pending

lolay/agent-images#21 is open, and this session watches it (CI, reviews). The owner
merges it; the merge starts s7. Before s8 the owner also creates the "Agent: Android"
environment in claude.ai and the `claude-env-android` secret.

## Resume

After the merge, watch `cloud-deploy`'s `vm image (android)` job: the Packer build
(does the build VM's nested KVM boot the emulator?), the smoke VM's boot times in the
run summary, and the promotion. Then s8 (the session test) once the environment and
secret exist. Remove this handoff and the plan, or promote them, before the m2 PR
that closes the milestone merges.

## Gotchas found while planning

- `images.patch` accepts `family`, so promotion is a patch, not an image copy.
- `maxRunDuration` on a standard VM requires `instanceTerminationAction`.
- Packer's `googlecompute` has `enable_nested_virtualization`, `use_iap`,
  `omit_external_ip`, `disable_default_service_account`; `use_iap` needs gcloud on the
  runner (setup-gcloud provides it).
- A disk restored from an image reads lazily, so the first emulator boot on a fresh VM
  may be much slower than the hosted runner's 9 s; the smoke VM times both boots.
