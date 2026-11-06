Fixes the terraform wrapper #31 added for SessionStart hooks on Anthropic's
cloud, and records the session that checked #31 and #32. Homebrew's `terraform`
is tenv's shim and runs `tenv` from PATH; the wrapper set `TENV_ROOT` but left
Homebrew off PATH, so under Claude Code's own PATH it failed with `exec: "tenv":
executable file not found in $PATH`. Spec: `specs/anthropic-cloud.md`.

## Review guide

Review tier: [check], because the wrapper changes the PATH Terraform runs with when a hook calls it.
Blast radius: only hooks calling `terraform` in Anthropic environments whose setup script runs this; the Bash tool already finds Homebrew's terraform first. Revert is safe.
Review time: ~2 min

1. [check] [`scripts/anthropic/setup.sh`][1]: the terraform wrapper's heredoc
2. [skim] [`specs/anthropic-cloud.md`][2]: the wrapper's PATH, and the check of #31 and #32

[1]: https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-81c438a0571c4fcec41494b81cdea7c33c447657f2e12bdb36bb0ee6d14a8809
[2]: https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-b0f56089017ac4cd60f50524cc3a0b5b54a604fdcc7a447748b9491c1d1e970b

## Validation

- `shellcheck scripts/anthropic/setup.sh`, and `shellcheck -s sh` on the generated wrapper: clean
- With a fake tenv shim and a hook-like PATH (`/usr/bin:/bin`): the old wrapper's behavior reproduces `tenv: not found`; the new wrapper runs `tenv` with `TENV_ROOT=/home/ubuntu/.tenv`, and keeps a caller's `TENV_ROOT`
- In a real session: not run. The environment needs a rebuild, which #32's notice should prompt

## Rollback

Revert the squash commit.

<details>
<summary>[check] The wrapper</summary>

| | Before | After |
| --- | --- | --- |
| `/usr/local/bin/terraform` | `TENV_ROOT=... exec <brew>/terraform` | `PATH="<brew>:$PATH" TENV_ROOT=... exec <brew>/terraform` |
| Under a hook's PATH | `tenv` not found, exit 1 | Runs ubuntu's selected Terraform |

The session that checked #31 and #32 (recorded in the spec): the doctor found `triage`, the nine linked tools ran under Claude Code's PATH, the fingerprint matched and the hook stayed silent, and a zeroed record produced the notice as JSON. Terraform was the one failure.

</details>
