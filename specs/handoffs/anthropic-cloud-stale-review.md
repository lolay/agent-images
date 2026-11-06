Warns a session when its Anthropic environment was built from an older
`scripts/anthropic/setup.sh` or `images/toolsets` than its checkout's. Anthropic
rebuilds its environment cache only when the environment's own setup script, its
allowed hosts or the week change, so a merged change here doesn't reach sessions
on its own: a session at 04:47 UTC started from a snapshot built before #31
merged at 04:46 and had none of its links. Spec: `specs/anthropic-cloud.md`,
"Cold start".

## Review guide

Review tier: [check], because the SessionStart hook every session runs gains a second job and can now print a notice.
Blast radius: only Anthropic environments whose setup script runs this. The hook fails quiet: no fingerprint, no checkout or no git means no notice. Revert is safe.
Review time: ~4 min

1. [check] [`scripts/anthropic/setup.sh`][1]: `build-fingerprint`, the recorded fingerprint, and the comparison in `session-env`
2. [skim] [`specs/anthropic-cloud.md`][2]: the cache doesn't follow the repo, and the notice

[1]: https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-81c438a0571c4fcec41494b81cdea7c33c447657f2e12bdb36bb0ee6d14a8809
[2]: https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-b0f56089017ac4cd60f50524cc3a0b5b54a604fdcc7a447748b9491c1d1e970b

## Validation

- `shellcheck scripts/anthropic/setup.sh`, and `shellcheck -s sh` on both generated hook files: clean
- Against a throwaway clone: same checkout, silent; a committed change to `setup.sh` or a toolset file, the notice as valid JSON (`systemMessage` and `additionalContext`); no recorded fingerprint, silent with exit 0; the `CLAUDE_ENV_FILE` line still written once
- An uncommitted edit doesn't trigger it (`git ls-files -s` hashes the index); sessions start from fresh clones
- In a real session: not run. The current environment's cache predates this, so its setup script needs one edit first

## Rollback

Revert the squash commit. Environments built with it keep the check until their cache rebuilds from the reverted script.

<details>
<summary>[check] What the hook does now</summary>

| | Before | After |
| --- | --- | --- |
| `CLAUDE_ENV_FILE` | Appends the profile line | The same |
| Stale build | Nothing; the session runs old tools silently | `systemMessage` to the user, and `additionalContext` telling Claude to pass it on |
| Inputs fingerprinted | n/a | `git ls-files -s scripts/anthropic/setup.sh images/toolsets`, hashed, recorded with the checkout path in `/opt/agent-images/build-fingerprint` |

The notice is fixed text: nothing from the repo or the checkout goes into it.

</details>

<details>
<summary>Decisions</summary>

- A notice, not a rebuild: the hook can't rerun a 4-minute install at session start, and Anthropic offers no API to invalidate the cache.
- Git blob hashes, not file contents: cheap at every session start, and they cover the toolsets' files without walking them.

</details>
