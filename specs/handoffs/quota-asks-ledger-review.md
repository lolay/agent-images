Records the two Cloud Run quota cases open with Google Cloud Support for `lolay-agents` (GPU, ephemeral disk): what we asked for, the numbers the replies gave, and the replies verbatim, so a later request stays consistent.

## Review guide

Review tier: [check], because these numbers are what Google gets told, and peak concurrency is an estimate.
Blast radius: none in code; one handoff doc. A revert is safe.
Review time: ~4 min

1. [check] [`specs/handoffs/infra-ask-m3-gpu.md`](https://github.com/lolay/agent-images/compare/main...claude/brave-edison-htqkgh#diff-6825598c3edfdab7fdedeefbff7748c3c5f0efb668107ac0c9624805148b24eb): item 3 rewritten, new "Google Cloud Support cases" section

## Validation

- Numbers checked against `infra/gcp/*.env` (CPU, memory, `TASK_TIMEOUT_SEC`, `MIN_IDLE`) and `specs/gcp.md`: match.
- No build or test: docs only.

## Rollback

Revert the squash commit.

<details>
<summary>[check] The asks</summary>

| Case | Before | After |
| --- | --- | --- |
| GPU | "request it", 1 to 3 of each | 3 L4 and 3 RTX PRO 6000, no zonal redundancy, `us-central1` |
| Ephemeral disk | "later", 200 GiB for `3d-gpu` before s10 | 100 GiB per instance now (Support's offer), 400 GiB regional; 200 GiB is a new request when `3d-gpu` is built |

Peak concurrency (3 GPU, 4 disk-using executions) is an estimate: nothing in the repo caps it. Check it matches what you expect.

</details>

<details>
<summary>Decisions</summary>

- Took Support's 100 GiB instead of holding out for 200: only `3d-gpu` needs 200, and it isn't built.
- Asked for the regional total up front, so it doesn't come back as another round of questions.
- Replies are marked drafted 2026-11-08, not sent; case numbers stay in email.

</details>
