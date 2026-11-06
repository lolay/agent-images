Gives SessionStart hooks on Anthropic's cloud the tools its image lacks, and
records the report that showed the Bash tool PATH fix (#29) working. Hooks run
with Claude Code's own PATH, not the one `CLAUDE_ENV_FILE` gives the Bash tool,
so the personal plugin's `make doctor` hook reported `triage` missing. Spec:
`specs/anthropic-cloud.md`, "Matching agent-linux on Anthropic's cloud".

## Review guide

Review tier: [check], because the setup script now changes what's in `/usr/local/bin` for every process in the environment, hooks included.
Blast radius: only Anthropic environments whose setup script runs this; the next session in each reruns the script. Revert is safe.
Review time: ~3 min

1. [check] [`scripts/anthropic/setup.sh`][1]: the symlink loop and the terraform wrapper at the end
2. [skim] [`specs/anthropic-cloud.md`][2]: the fresh-environment report, and why hooks miss the PATH

[1]: https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-81c438a0571c4fcec41494b81cdea7c33c447657f2e12bdb36bb0ee6d14a8809
[2]: https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-b0f56089017ac4cd60f50524cc3a0b5b54a604fdcc7a447748b9491c1d1e970b

## Validation

- `shellcheck scripts/anthropic/setup.sh` (0.9.0): clean
- The link loop against fake directories: links every tool Homebrew has, skips one it doesn't, leaves an existing file in place
- The terraform wrapper as generated: parses with `sh -n`
- In a real session: not run. The next session reruns the script; the session-start doctor output should no longer list `triage` as missing

## Rollback

Revert the squash commit. Environments built with it keep the links until their cache rebuilds from the reverted script.

<details>
<summary>[check] What hooks see</summary>

| In a hook's PATH | Before | After |
| --- | --- | --- |
| triage, nowline, sops, shellcheck, actionlint, pre-commit, protoc, buf, grpcurl | Missing | Symlinks in `/usr/local/bin` to Homebrew's |
| terraform | Missing | A wrapper that supplies `TENV_ROOT` |
| Node, Go, Python, Rust | Anthropic's | Unchanged: Anthropic's |
| Anything already in `/usr/local/bin` | | Never replaced |

The Bash tool is unaffected: Homebrew's directories are ahead of `/usr/local/bin` there.

</details>

<details>
<summary>Decisions</summary>

- Only tools Anthropic lacks altogether: linking Node or Go would change what Anthropic's own tooling and hooks run, not just add what's missing.
- No PATH change for hooks: they inherit Claude Code's process environment, which the setup script can't reach.

</details>
