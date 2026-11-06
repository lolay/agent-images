Incremental review of `main` from 506a1b9 to 28ca834 (10 commits, 3,670 changed lines), following r1. Covers m4 (lolay/agent-images#48: binary checks, smoke tests and the versions report) and #38, #41 to #47. Register: `2026-11-06-r2-incremental-findings.md`.

## Review guide

Review tier: [skim], records and one `AGENTS.md` rule; the decisions below are the reading.
Verdict: 1 medium, 12 low, 3 info; 2 decisions for you. Nothing high. The verifier dropped 4 of 21 and lowered 4.
Review time: ~6 min

m4 holds up: one runner (`toolset-checks.sh`) for the cloud entrypoint, the android smoke VM and the session skill, with its timeout, temp directory and offline settings in one place, and every type's report in the PR and deploy summaries. The weak spot is the tests: four guards can be deleted without a test going red.

## Fix now

- r2.f1 medium: an empty toolset record passes every gate (PR check, `-next` smoke, android smoke VM), and nothing tests `install-toolset.sh`'s `record()`, which writes it

## Decisions for you

1. **r2.f11 The android versions report stops at the Packer log.** Now: Cloud Run types get the report in the deploy summary and a 90-day artifact; android's is in the image and the Packer log only, while design.md, gcp.md's "Keep one image" section and the m5 handoff say every image's lands in the summary. Options: download it from the Packer build and add the same summary and artifact steps to `vm-images` (recommended: m5's lock reads these reports and covers android), or narrow the three passages to Cloud Run.
2. **r2.f16 The report and record paths are spelled out in several places** (info). Recommendation: make write-versions.sh the one writer of `/etc/agent-images/versions.md`, and set `AGENT_TOOLSETS_DIR` once in the profiles; leave the prose copies sessions need.

## Since last review

```mermaid
flowchart LR
  subgraph before[506a1b9]
    e1[entrypoint smoke: check.txt loop] --> c1[check.txt]
    g1[agent-gce-smoke: hand-listed checks]
    w1[android-image-smoke.yml: hand-listed checks]
  end
  subgraph after[28ca834]
    e2[entrypoint smoke / smoke pr] --> r[agent-toolset-checks]
    g2[agent-gce-smoke] --> r
    s[/agent-toolset-checks skill/] --> r
    r --> c2[check.txt: binary checks]
    r --> sm[smoke.sh: smoke tests]
    v[write-versions.sh] --> rep[/etc/agent-images/versions.md]
    rep --> sum[PR and deploy summaries, artifact]
    w2[android-image-smoke.yml: still hand-listed, r1.f27]
  end
```

Also in range: `infra/gcp/*.env` as the one type list (#44), Claude upgraded by platform and shared runner settings (#45), SHA-pinned Actions with Renovate (#43), `DEPLOY=false` for linux-gpu until its quota lands (#47).

## Rules changed

- Added to `AGENTS.md`, "Tests that can fail": a test for a guard must go red when the guard is deleted. Four findings in this review are this class: r2.f1, f2, f3, f6.
- "Test fakes" (r1) recurred once (r2.f4); its citation now names it.

<details>
<summary>Fix review</summary>

Of r1's 26 open findings, 1 is fixed and 25 are still open; none has a recorded decision.

| Finding | Was | Now | By |
| --- | --- | --- | --- |
| r1.f8 | open | fixed | #48: smoke execution `--task-timeout=1800s`, `types` job `timeout-minutes: 120` |
| r1.f27 | open | open, partly fixed | #48: one check runner. Left: android PATH and profile assembly, `android-image-smoke.yml`'s hand list |
| the other 24 | open | open | code unchanged at their locations |

r1's log row now reads 9 fixed, 25 open. r2.f9 adds `DEPLOY` as a second per-key reader to r1.f33; r2.f15 is a leftover of r1.f19.

</details>

<details>
<summary>Details by dimension</summary>

- **Correctness** (`/code-review high` on the range, f6 to f9): a TERM-ignoring smoke test exits 137, not 124 (f6); a failing `dpkg-query` fails the image build (f7); gis's smoke test drops its traceback (f8); `DEPLOY=false` stops a compute type's orchestrator but not its deploy (f9, latent).
- **Tests** (f1 to f5): 252 tests, 1 skipped, all green. Each finding was shown by a mutation that stayed green. Fake gcloud subcommands (f4); temp repos inherit the host's git signing config (f5).
- **Drift** (f10 to f12): the LXD android build gets none of m4's runner, skill or report (f10); stale docs and eleven `check.txt` headers (f12).
- **Duplication** (f13 to f16): `/opt/gis` copied into write-versions.sh (f13); no make target builds the report (f14, info); the entrypoint's retire-margin default (f15, info).
- **Dropped:** the android smoke VM's 1500 s budget, the `plan` job's tag pin (already in r1.f15), a bake matrix, the skill's "120 s".

Not covered: real `-next` and android smoke runs, which only `main` can do (m5 handoff, "Still open from m4").

</details>

Previously accepted, not raised again: none.

Assisted-by: Claude Code
