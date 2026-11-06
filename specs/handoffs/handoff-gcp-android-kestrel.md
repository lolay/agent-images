# Handoff: m2 `android` on Compute Engine (session)

Branch `claude/youthful-dijkstra-09v7kk` (assigned by the harness), from `main` at
`ecc54c2`. Plan: [plan-gcp-android-kestrel.md](plan-gcp-android-kestrel.md).

## Done

- Checked that lolay/agent-images#18 merged; cut the branch from the latest `main`.
- Wrote the plan (s1 to s8); built s1 to s6 (commits m2.s1 to m2.s6), and the PR draft `plan-gcp-android-kestrel-review.md`.

## Pending

s7 is done: `android` is deployed (run 18, 2026-11-06), with the image in family
`agent-android` and the orchestrator carrying the hook, reaper and `android.env`.
s8 is the owner's: start a session in "Agent: Android" and run
`tests/sessions/android.md`. Then record its results in specs/gcp.md ("m2 `android`
deploy" gets a session-test table), set `EXPECTED_SPAWN_SECONDS` from the measured
spawn, and write the GCP-or-Linux-host recommendation.

Leftovers in `lolay-agents` the owner was asked to delete by hand: `packer-*` disks
(from runs 13 and 14) and run 14's build VM if it hasn't deleted itself.

## Gotchas found while planning

- `images.patch` accepts `family`, so promotion is a patch, not an image copy.
- `maxRunDuration` on a standard VM requires `instanceTerminationAction`.
- Packer's `googlecompute` has `enable_nested_virtualization`, `use_iap`,
  `omit_external_ip`, `disable_default_service_account`; `use_iap` needs gcloud on the
  runner (setup-gcloud provides it).
- A disk restored from an image reads lazily, so the first emulator boot on a fresh VM
  may be much slower than the hosted runner's 9 s; the smoke VM times both boots.
