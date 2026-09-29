# Cursor

Status: **documented, not implemented.** agent-images supports Claude only for now.
The last commit with a working Cursor runner is `439eb5a` (`files/runners/cursor.sh`,
`vms/example-cursor.env`, the `cursor-cli` cask, and the `cursor-api-key` / `git-token`
secrets in `vm-configure.sh`). Start there if Cursor comes back.

Researched 2026-09-28 from Cursor's docs; the flags were not exercised on a real VM.

## How Cursor's self-hosted workers differ from Claude's runner

| | Claude self-hosted runner | Cursor My Machines (any plan) | Cursor Team Pools (Enterprise only) |
| --- | --- | --- | --- |
| What runs on the VM | The whole Claude Code session | Tool calls only (terminal, file edits, browser); the agent loop and inference stay in Cursor's cloud | Same as My Machines |
| Repos | Any repo a session asks for; the runner clones it | Bound to the `--worker-dir` checkouts (repeatable) | Repo-backed, or any-repo with `--clone-git-repos` |
| One session per VM | Built in: `--capacity 1`, `--drain-grace-sec 0` | No. "Long-lived: it stays connected until you stop it and can be reused for future Cloud Agent sessions" | `--idle-release-timeout <sec>` (default 3600) exits 0 after a session goes idle; a supervisor recycles the machine |
| On-demand VMs | `claude self-hosted-runner orchestrator` + `spawn-runner` hook, `--min-idle` | None | `agent worker controller --spawn ./spawn.sh --warm-idle N` |
| Credential on the VM | Single-use work order (orchestrator) | Personal API key, long-lived, readable by every session | Service account key, or per-claim session tokens from `/v0/private-workers/tokens` |
| Git auth | `--use-anthropic-git-proxy` | Whatever the VM has (a token we'd push in) | `--mint-github-token`, short-lived, covers the request's repos |
| Secrets for sessions | None built in | Not documented | `--sync-dashboard-secrets`: dashboard secrets as env vars during claimed runs |
| macOS | Yes | Yes | Yes |
| Command | `claude self-hosted-runner` | `agent worker start` | `agent worker --pool <name> start` |

The Homebrew `cursor-cli` cask installs the binary as `cursor-agent`; Cursor's docs and
native installer call it `agent`.

## Why it's out for now

- **My Machines can't give a clean VM per session.** The worker is long-lived and
  repo-bound, and the VM would hold a personal API key every session can read. Making
  it ephemeral would need our own session-end detection, which Cursor doesn't document.
- **It doubles the host side.** Claude uses the orchestrator; Cursor would need the
  warm loop, repo sync, and git-token handling on top.
- **Team Pools would fit, but need Enterprise.** Their model matches ours closely.

## Adding it back (Team Pools)

1. Restore `files/runners/cursor.sh` from `439eb5a` as `agent-runner-cursor`, switched
   to pool mode: `cursor-agent worker --pool <name> --idle-release-timeout <sec>
   --clone-git-repos --mint-github-token start`, with a service account key.
2. Add `cask "cursor-cli"` back to the Brewfile; the runner script upgrades it at start.
3. Host: run `cursor-agent worker controller --spawn <hook> --warm-idle 1` as a
   LaunchAgent. Its spawn hook starts one ephemeral VM the same way the Claude
   `spawn-runner` hook does, passing the per-claim token instead of a long-lived key.
4. `vm-configure.sh`: accept `AGENT=cursor` and push the token.

## Sources

- [Self-Hosted Machines](https://cursor.com/docs/cloud-agent/self-hosted)
- [My Machines](https://cursor.com/docs/cloud-agent/self-hosted/my-machines)
- [Team Pools](https://cursor.com/docs/cloud-agent/self-hosted/pool)
