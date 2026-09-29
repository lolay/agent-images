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
| Claude and Cursor only | The only vendors with self-hosted runners today (§6) |
| One runner, one simulator per VM | Keeps sizing predictable: ~12 GB per VM |
| Ephemeral VMs: one session per clone | A session can `brew install` or decrypt secrets without affecting the next, like a GitHub Actions runner |
| One image; runner chosen at configure time | The Xcode image is ~150 GB; variants would differ by almost nothing |
| Secrets in the host Keychain, pushed over `tart exec` stdin | Never in the repo, the image, or a process list |
| Cursor worker bound to a workspace repo for multi-repo projects | A worker serves one repo; submodules fight cross-repo changes |
| The agent user owns Homebrew; runner CLIs are casks | Homebrew's standard single-owner setup; sessions can `brew install` what a project needs |
| Each runner upgrades only its own cask at start | Rebuilds would otherwise be weekly; a Claude VM doesn't pay for Cursor updates |
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
   own CLI (`claude-code@latest` or `cursor-cli`) and then starts the runner.
4. The runner exits (Claude exits after every session by default). With
   `EPHEMERAL=true`, `agent-runner` then runs `sudo /sbin/shutdown -h now`, the agent
   user's only sudo rule (`/etc/sudoers.d/agent-shutdown`). Without it (a persistent
   VM from `make vm-up`), launchd restarts the runner, throttled to one start per 30 s.

| File in the guest | Written by | Mode |
| --- | --- | --- |
| `~/.config/agent-runner/runner.env` | `vm-configure` from `vms/<vm>.env` | 600 |
| `~/.claude-runner/environment-secret` | `vm-configure` from Keychain | 600 |
| `~/.config/agent-runner/secrets/cursor-api-key` | `vm-configure` from Keychain | 600 |
| `~/.config/agent-runner/secrets/git-token` | `vm-configure` from Keychain (optional) | 600 |

`runner.env` is written last, so launchd never starts a runner with missing secrets.

## 5. Runners

**Claude**: `claude self-hosted-runner --environment-secret-file … --client-label <vm>
--base-dir ~/workspace --capacity 1 --remove-session-state`, plus
`--lock-to-account` if set and `--use-anthropic-git-proxy` by default (Anthropic-managed
git auth; it replaces the agent user's `~/.gitconfig`). `/healthz` on port 8080.

**Cursor**: `cursor-agent worker start --worker-dir … --name <vm>` for My Machines, or
`cursor-agent worker --pool --pool-name … start` for team pools, with `CURSOR_API_KEY`. The
worker directory is a full clone of `CURSOR_REPOSITORY_URL`, fetched on every start.
Cursor's worker API reports one in-use session per worker, which is what keeps one
worker per VM at one simulator.

## 6. Vendor landscape (2026-09-28)

| Agent | Cloud hosted | Self-hosted runner | GitHub Action | Mac |
| --- | --- | --- | --- | --- |
| Claude | Yes | Strong | Yes | Self-hosted, Actions |
| Cursor | Yes | Weak (per repo) | No official Action | Self-hosted, unconfirmed |
| Copilot | Yes | Strong | Yes | None (Linux/Windows runners only) |
| Codex | Yes | Experimental | Yes | Actions |
| Gemini | Yes (Jules) | None | Yes | Actions |
| OpenCode | No | None | Yes | Actions |

A GitHub Actions self-hosted runner mode would cover Codex, Gemini, and OpenCode; not
built yet.

## 7. Open items to verify on first build

| Item | Why |
| --- | --- |
| Cursor supports macOS workers | Every official example is a Linux container |
| `agent worker --name` exists in both modes and is unique enough | Pool mode doesn't pass it; names come from the hostname, set per VM |
| The `cursor-cli` cask's `cursor-agent` has the `worker` subcommand | Cursor's examples call it `agent` (native installer) |
| `brew bundle` works as `agent` after the `/opt/homebrew` chown | The base image installs Homebrew as `admin` |
| Casks install without a Gatekeeper prompt in a headless build | First cask installs in this image |
| `--remove-session-state` accepts the bare form | Help shows `[bool]` |
| `/etc/kcpassword` + `autoLoginUser` override the base image's admin auto-login | Golden Gate sets auto-login through Tart's provisioning options |
| `tart exec` runs as a user with passwordless sudo | Every `vm-*` script relies on it |
| Pin `base_image` to a specific Xcode tag | `latest` moves |
| A Cursor worker exits after one session | If not, a Cursor runner is persistent in practice; its management API (`127.0.0.1:8081`) may show when a session ends |
| Claude's runner exits when idle only after a session, never before one | Otherwise an idle VM would recycle without having served anything |

