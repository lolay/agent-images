<!--
A review guide (personal standards, review.md): a reviewer should understand
the change from everything above the first <details> in about 2 minutes on a
phone. Tag each area [study], [check], [skim] or [skip]; the PR's tier is the
highest. Drop any section or tier with nothing to say.

Repo rules are in AGENTS.md (mirror.gcr.io for Docker Hub images, one source per
fact, test fakes that reject what the real API rejects, tests that go red when
the guard is removed). Run `make pre-commit` before pushing.
-->

What changes and why, in one paragraph. Closes #

## Review guide

Review tier: [check], because <reason>.
Blast radius: <who or what breaks if it's wrong; how reversible>.
Review time: ~N min

1. [check] `path`: <what to look at>
2. [skim] `path`

## Validation

- `make pre-commit`: pass, fail, or not run (why)
- `make build`: pass, fail, or not run (why; only for image or provisioning changes)

## Rollback

<how to undo it: revert, flag, migration>

<details>
<summary>[check] Control flow and error handling</summary>

<before/after for this tier's areas: contracts table, Mermaid diagrams, data, error handling>

</details>

<details>
<summary>Decisions</summary>

<alternatives ruled out, and why>

</details>

<!-- If an agent helped, end with the trailer paragraph: Assisted-by: <tool> -->
