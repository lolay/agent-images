Fixes `scripts/anthropic/setup.sh` so `agent-linux`'s tools reach the commands
Claude actually runs on Anthropic's cloud. A report from a cached session showed
login and interactive shells with every `agent-linux` tool at `agent-linux`'s
version, but Claude Code's Bash tool on Anthropic's PATH: eight smoke checks
failed there, Rust was root's 1.97, and Terraform failed as root. Spec:
`specs/anthropic-cloud.md`, "Matching agent-linux on Anthropic's cloud".

## Review guide

Review tier: [study], because the script now installs a SessionStart hook that runs in every session and changes the environment of every Bash tool command.
Blast radius: only Anthropic environments whose setup script runs this; changing the script invalidates their cache, so the next session pays the script once. Revert is safe.
Review time: ~6 min

1. [study] [`scripts/anthropic/setup.sh`][1]: profile fragment (`RUSTUP_HOME`, `TENV_ROOT`, guarded PATH), the hook script, the `settings.json` merge
2. [skim] [`specs/anthropic-cloud.md`][2]: two more differences from `agent-linux`, and the report's findings

[1]: https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-81c438a0571c4fcec41494b81cdea7c33c447657f2e12bdb36bb0ee6d14a8809
[2]: https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-b0f56089017ac4cd60f50524cc3a0b5b54a604fdcc7a447748b9491c1d1e970b

## Validation

- `shellcheck scripts/anthropic/setup.sh`: clean
- The generated profile fragment, sourced twice: PATH has the agent-images entries once
- The `settings.json` merge with jq: adds the hook to `{}`, keeps an existing SessionStart hook, and doesn't duplicate its own on a second run
- As root with `RUSTUP_HOME` and `TENV_ROOT` pointed at the user that installed the toolsets: `cargo 1.99.1`, `rustc 1.99.1`, `Terraform v1.17.1`
- The hook in a real session: not run. It needs a cache rebuild; the report prompt's steps 3, 4 and 6 check it

## Rollback

Revert the squash commit. Environments already built with it keep the hook until their cache rebuilds from the reverted script.

<details open>
<summary>[study] What a session's environment gets</summary>

| | Before | After |
| --- | --- | --- |
| Bash tool PATH | Claude Code's own (Anthropic's Node 22, Go 1.24, no sops, protoc, Terraform) | Homebrew's first, through `$CLAUDE_ENV_FILE` |
| `/root/.claude/settings.json` | Absent | One SessionStart hook: `/opt/agent-images/libexec/session-env` |
| `RUSTUP_HOME` | `/root/.rustup` (Anthropic's, 1.97) | `/home/ubuntu/.rustup` (1.99) |
| `TENV_ROOT` | Unset, so root had no Terraform selected | `/home/ubuntu/.tenv` |

Why a hook: Claude Code's shell snapshot ends with `export PATH=` Claude Code's own PATH, so `/etc/profile.d` and `~/.bashrc` never reach the Bash tool. `CLAUDE_ENV_FILE` is the documented way for a SessionStart hook to add to every command's environment; the `agent-linux` image's hook already uses it. Anthropic's own hooks are in `launcher-settings.json`, so `settings.json` was free.

Root writing under `/home/ubuntu/.rustup` or `.tenv` later (a `rustup update`, a `.terraform-version` download) leaves root-owned files there; nothing runs those as `ubuntu` in a session.

</details>

<details>
<summary>Decisions</summary>

- Pointing root at `ubuntu`'s toolchains rather than installing Rust and Terraform a second time as root: no extra setup time, and the first run already used 246.5 s of the five-minute cache budget.
- Not `BASH_ENV` or the environment's PATH variable: the snapshot's final `export PATH` overrides the first, and the second would hardcode Anthropic's whole PATH.

</details>
