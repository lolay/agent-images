# Design

## 1. Goal

Mac build capacity for coding agents that the vendor's service dispatches work to.
Nobody works inside these VMs, even over SSH. The image supplies Xcode, Simulator, and
a logged-in GUI session; the vendor's runner supplies the work.

## 2. Constraints

| Constraint | Consequence |
| --- | --- |
| Two running macOS guests per host (Apple license, enforced by Virtualization.framework) | Scale with more hosts, not more VMs |
| Simulator and UI tests need a GUI login session | One auto-login user per VM; the runner is a LaunchAgent in that session |
| Guest macOS can't be newer than the host | Hosts and guests on Golden Gate |
| Cirrus base images symlink `/Users/runner` to `/Users/admin` | The user is `agent` |
| Hosted GitHub macOS runners can't nest VMs | CI lints and validates; images build on a Mac |
| `/workspace` can't be created on macOS's read-only system volume | Claude's `--base-dir` is `~/workspace` |

## 3. Decisions

| Decision | Why |
| --- | --- |
| Runner model only; no Remote Control | Headless, no interactive login, fits dispatch from the vendor's UI |
| Claude only for now | The only runner that gives a clean VM per session without an Enterprise plan; Cursor is documented in [cursor.md](cursor.md) |
| One runner, one simulator per VM | Keeps sizing predictable: ~12 GB per VM |
| Ephemeral VMs: one session per clone | A session can `brew install` or decrypt secrets without affecting the next, like a GitHub Actions runner |
| One image; runner chosen at configure time | The Xcode image is ~150 GB; variants would differ by almost nothing |
| Secrets in the host Keychain, pushed over `tart exec` stdin | Never in the repo, the image, or a process list |
| Sessions push their work before an early end | `--push-outcome-on-release` plus a Stop hook in the image's `~/.claude` that asks Claude to commit and push; a fresh VM per session otherwise loses unpushed work |
| Git proxy watchdog, log-only at first | [anthropics/claude-code#96856](https://github.com/anthropics/claude-code/issues/96856) breaks git for the rest of a session; `WATCHDOG_ACTION=terminate` requeues it onto a fresh VM once detection is proven |
| No secrets for sessions | Parity with Anthropic-hosted environments; secret-needing work stays in GitHub Actions ([secrets.md](secrets.md)) |
| The agent user owns Homebrew; runner CLIs are casks | Homebrew's standard single-owner setup; sessions can `brew install` what a project needs |
| The runner upgrades its own cask at start | Rebuilds would otherwise be weekly; each `AGENT` upgrades only its own CLI |
| File ownership isn't an isolation boundary | Sessions run as `agent`, which owns its home and Homebrew; the fresh clone per session is the reset |
| The host loop is a shell script plus a LaunchAgent | Tart only on the host; Orchard or a vendor orchestrator can replace it later |
| Runners start at host login | Hosts are Macs with a display; set the host user to log in automatically for unattended restarts |

## 4. How a runner runs

On the host, `scripts/runner-run.sh <vm>` (a LaunchAgent via `make runner-install`)
loops:

1. `tart clone` the image to `<vm>` (APFS copy-on-write) and size it.
2. `tart run --no-graphics` in the background.
3. `EPHEMERAL=true vm-configure <vm>`: hostname, secrets, `runner.env`.
4. Wait for `tart run` to exit (the guest powers itself off), then `tart delete`.
5. Back off 60 s if the VM lived under 60 s, so a bad secret doesn't spin; repeat.

The VM name is the runner's identity, reused every cycle. The loop refuses to start
if a VM by that name already exists, and stopping it deletes its VM.

In the guest:

1. Boot → `agent` auto-logs in → LaunchAgent `com.agent-images.runner` loads.
2. launchd runs `~/bin/agent-runner` through `zsh -l`, so `PATH` comes from
   `~/.zprofile` (`brew shellenv`, then `~/bin`). It reads `~/.config/agent-runner/runner.env`. If it's
   missing, it exits and launchd waits for the file (`KeepAlive` / `PathState`).
3. It execs `~/bin/agent-runner-<AGENT>`, which runs `brew upgrade --cask` for its
   own CLI (`claude-code@latest`) and then starts the runner.
4. The runner exits (Claude exits after every session by default). With
   `EPHEMERAL=true`, `agent-runner` then runs `sudo /sbin/shutdown -h now`, the agent
   user's only sudo rule (`/etc/sudoers.d/agent-shutdown`). Without it (a persistent
   VM from `make vm-up`), launchd restarts the runner, throttled to one start per 30 s.

Alongside, LaunchAgent `com.agent-images.watchdog` runs `~/bin/agent-runner-watchdog`
every 30 s. It reads each session's git proxy port from
`~/workspace/_sessions/<id>.gitconfig`; two refused connections in a row mean the relay
is gone. It logs to `~/Library/Logs/agent-runner-watchdog.log`, and with
`WATCHDOG_ACTION=terminate` sends the runner SIGTERM so the session requeues.

| File in the guest | Written by | Mode |
| --- | --- | --- |
| `~/.config/agent-runner/runner.env` | `vm-configure` from `vms/<vm>.env` | 600 |
| `~/.claude-runner/environment-secret` | `vm-configure` from Keychain | 600 |

`runner.env` is written last, so launchd never starts a runner with missing secrets.

## 5. Runners

**Claude**: `claude self-hosted-runner --environment-secret-file … --client-label <vm>
--base-dir ~/workspace --capacity 1 --remove-session-state`, plus
`--lock-to-account` if set and `--use-anthropic-git-proxy` by default (Anthropic-managed
git auth; it replaces the agent user's `~/.gitconfig`). `/healthz` on port 8080.

**Cursor**: not implemented; see [cursor.md](cursor.md).

## 6. Vendor landscape (2026-09-28)

| Agent | Cloud hosted | Self-hosted runner | GitHub Action | Mac |
| --- | --- | --- | --- | --- |
| Claude | Yes | Strong | Yes | Self-hosted, Actions |
| Cursor | Yes | My Machines (long-lived, per repo); Team Pools (Enterprise) | No official Action | Self-hosted ([cursor.md](cursor.md)) |
| Copilot | Yes | Strong | Yes | None (Linux/Windows runners only) |
| Codex | Yes | Experimental | Yes | Actions |
| Gemini | Yes (Jules) | None | Yes | Actions |
| OpenCode | No | None | Yes | Actions |

A GitHub Actions self-hosted runner mode would cover Codex, Gemini, and OpenCode; not
built yet.

## 7. Open items to verify on first build

| Item | Why |
| --- | --- |
| `brew bundle` works as `agent` after the `/opt/homebrew` chown | The base image installs Homebrew as `admin` |
| Casks install without a Gatekeeper prompt in a headless build | First cask installs in this image |
| `--remove-session-state` accepts the bare form | Help shows `[bool]` |
| `/etc/kcpassword` + `autoLoginUser` override the base image's admin auto-login | Golden Gate sets auto-login through Tart's provisioning options |
| `tart exec` runs as a user with passwordless sudo | Every `vm-*` script relies on it |
| Pin `base_image` to a specific Xcode tag | `latest` moves |
| The watchdog's inputs exist: `_sessions/<id>.gitconfig` with `http.https://github.com/.proxy` | Runner internals from #96856; if they move, the watchdog silently finds nothing |
| The runner process's command line starts `claude self-hosted-runner` | The watchdog's `pkill -f` pattern |
| The Stop hook reaches sessions from `~agent/.claude` | The runner seeds that directory at startup; check a session's `$CLAUDE_CONFIG_DIR/hooks/` |

