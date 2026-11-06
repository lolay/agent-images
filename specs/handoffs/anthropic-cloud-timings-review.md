Records the cold starts measured with `scripts/anthropic/setup.sh` on Anthropic's
cloud, and turns the comparison's summary into a side-by-side table of the
differences between Anthropic's environment (with and without `agent-linux`'s
tools) and `agent-linux`, with no recommendation. Follows #24. Spec:
`specs/anthropic-cloud.md`.

## Review guide

Review tier: [skim], because it's one docs file with no behavior change.
Blast radius: none; nothing deploys or runs. Revert is safe.
Review time: ~3 min

1. [skim] [`specs/anthropic-cloud.md`][1]: the Summary table, Cold start, and the cache paragraph

[1]: https://github.com/lolay/agent-images/pull/PRNUM/files#diff-b0f56089017ac4cd60f50524cc3a0b5b54a604fdcc7a447748b9491c1d1e970b

## Validation

- Three sessions in one Anthropic environment on 2026-11-06, timed by hand: 3:55 (the script, 246.5 s), 2:45 (the script again, 166 s, started 17 s after the first finished), about 12 s (from the cache, 54 minutes later)
- The cache hit is confirmed by the third session's setup log: it was the first run's, restored
- The setup script's `brew` wrapper and PATH change in a session: not checked yet

## Rollback

Revert the squash commit.

<details>
<summary>[skim] What changed in the spec</summary>

- Summary: a table of 15 differences across the three setups, replacing the prose that weighed them.
- Cold start: the three measurements, and that the snapshot is of the first successful run.
- The 246.5 s run leaves under a minute of Anthropic's five-minute cache budget; stated with the two ways to cut it, not acted on.
- Removed: "So the cost question is only about compute", "An experiment, not a decision", and the other judgement lines.

</details>
