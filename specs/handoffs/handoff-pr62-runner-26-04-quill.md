# Handoff: PR 62 triage, Actions runner ubuntu-24.04 to ubuntu-26.04

Branch: `claude/relaxed-newton-tonbso` (harness-assigned task branch, cut from main at c2c8999).
Subject: lolay/agent-images#62 (Renovate, head 3743b22). Nothing changed on the PR: no merge, close or comment.

## Status

Decision changed: pin, not fix. The Owner asked what 26.04 runners buy; the answer was close to nothing
(builds run in Docker, 24.04 runners are supported into 2028+, the shellcheck match doesn't close r1.f17),
against an actionlint workaround, a Renovate exception and a runner/image OS split (r1.f18).

Done on this branch: `.github/renovate.json` holds every `ubuntu` runner (github-runners datasource), with the
reason and what lifts it; the android-image-smoke comment ties that runner to the Android image base. The
earlier `.github/actionlint.yaml` is removed. Verified with Renovate 44.235.1's local dry run: all 14 runners
`disabled` with the rule, all 14 proposing 26.04 without it. Config validator passes.

Pending, each on the Owner's go: open the PR (this handoff removed as its last commit); close PR 62.
Lifting the pin: when Cloud Run's driver supports CUDA 13.3 or later (the first with ubuntu26.04 images),
move every image and runner to 26.04 together and delete the rule.

## Findings

- `check` fails in `make cloud-lint`, at actionlint v1.7.12: `label "ubuntu-26.04" is unknown`, 13 times across
  cloud-build, cloud-deploy and lock-watch. `make` stopped there, so `cloud-test` and `packer validate` never ran.
- `gate` only aggregates: `check: failure, build: success`. No second cause.
- Nothing 26.04 broke. All four `build (*)` jobs (Docker 29, buildx) ran green on the real 26.04 image
  (20261101.186.1). shellcheck 0.11 and shfmt 3.12 from 26.04's apt passed.
- No actionlint release knows the label: v1.7.12 is the newest on the Go proxy, and actionlint's main
  (2026-04-19) lists only 24.04 and 22.04.
- Locally, on PR head: actionlint v1.7.12 with `self-hosted-runner.labels: [ubuntu-26.04]` passes on all four
  workflows; `python3 -m unittest discover -s tests/unit` passes (376 tests, 20 skipped, Python 3.15.2).
- 26.04 is GA on GitHub (actions/runner-images issue 14747); `ubuntu-latest` moves to it in November 2026 (14748).
  The 26.04 image keeps what deploy and lock-watch use: Homebrew 7.0.11 (off PATH, as on 24.04), Docker, gcloud 591.
- Host-independent: lock.sh resolves apt inside the base image container, not on the runner.
- Host-coupled: `android-image-smoke.yml` runs `images/android` scripts directly on the runner as the stand-in for
  the Android image's Ubuntu 24.04 VM base (specs/linux.md lines 4-6; images/android/gce/build.pkr.hcl:20).
  On 26.04 it would test a different OS than the image ships. This is where r1.f18 touches the runner.
- Unexercised by PR checks: cloud-deploy (auth, setup-gcloud, vm-images) and lock-watch run only from main.
  The nightly deploy (04:07 PT, Tue to Sat) is their first 26.04 run.

## Gotchas

- `gh api .../actions/jobs/<id>/logs` needs `--allow-escape-sequences` here.
- Pushing to Renovate's branch makes Renovate stop updating it; land fixes on main instead.
