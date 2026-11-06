Records the session that checked #33 on Anthropic's cloud, and spells out which
route delivers the agent-images profile to which shell. That session's report
read the `~/.bashrc` line as a route into the Bash tool; it isn't one, and the
spec now says so. Docs only: `setup.sh` is unchanged, so no environment's build
fingerprint moves and nothing needs a rebuild. Spec: `specs/anthropic-cloud.md`.

## Review guide

Review tier: [skim], because it's one spec file with no behavior change.
Blast radius: none; nothing deploys or runs. Revert is safe.
Review time: ~2 min

1. [skim] [`specs/anthropic-cloud.md`][1]: the post-#33 check, and which route reaches which shell

[1]: https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-b0f56089017ac4cd60f50524cc3a0b5b54a604fdcc7a447748b9491c1d1e970b

## Validation

- The session after #33, 2026-11-06: the script ran in 224.3 s; under Claude Code's own PATH all nine linked tools and `terraform` (1.17.1) ran; the fingerprint matched and the hook was silent; a zeroed record produced the notice; all 32 smoke checks passed in the Bash tool's shell
- `setup.sh` was edited and then restored on this branch: its diff against `main` is empty

## Rollback

Revert the squash commit.

<details>
<summary>[skim] What the spec adds</summary>

| Shell | Gets the profile from |
| --- | --- |
| Login | `/etc/profile.d/zz-agent-images.sh` |
| Interactive (`bash -ic`) | The line in `/root/.bashrc` |
| Claude Code's Bash tool | Only the SessionStart hook, through `CLAUDE_ENV_FILE` |

`CLAUDE_ENV_FILE` is set for hooks, not in the commands' environment, so its absence there is expected. Also: the 224.3 s run, and the range of the five full runs (166 to 247 s).

</details>
