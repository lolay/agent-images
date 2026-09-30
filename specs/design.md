# Design

## 1. Goal

Mac build capacity for coding agents that the vendor's service dispatches work to.
Nobody works inside these VMs, even over SSH. The image supplies Xcode, Simulator, and
a logged-in GUI session; the vendor's runner supplies the work.

### Track the latest; pin only on breakage

This is an informal development environment. Everything in it follows the newest
release by default, the way `claude-code@latest` does. Staying current matters more
than stability here, so breakage from a new release is something to see and fix, not
something to avoid by pinning.

| What | How it stays current |
| --- | --- |
| Base image | `make build` pulls the newest Xcode image for the host's macOS |
| Homebrew packages (`images/macos/Brewfile`) | No versions in the Brewfile; each `make build` installs the current release |
| Claude Code | The `claude-code@latest` cask, upgraded by the runner at every VM start |
| Anything a session installs | Whatever Homebrew has that day, discarded with the VM |

Everything except Claude Code moves only when the image is rebuilt, so rebuild
regularly. It's safe while runners are up: Packer builds `agent-macos-next`, and only a
finished build is swapped into `agent-macos`, which new runner VMs then clone on their
own.

When a release does break something, pin just that one thing: a versioned formula or a
base image digest, with a comment saying why and linking the upstream issue. Remove
the pin once upstream is fixed. The README's version table records what was current at
the last lookup; it doesn't pin anything.

The CI gate is the exception. GitHub Actions stay on release tags (a supply-chain
guard), and CI installs the Packer version that `required_version` asks for, so a
failing `make ci` points at a change in this repo rather than a tool update.

## 2. Constraints

| Constraint | Consequence |
| --- | --- |
| Two running macOS guests per host (Apple license, enforced by Virtualization.framework) | Scale with more hosts, not more VMs |
| Simulator and UI tests need a GUI login session | One auto-login user per VM; the runner is a LaunchAgent in that session |
| Guest macOS can't be newer than the host | Hosts and guests on Golden Gate |
| Cirrus base images log `admin` in automatically, with Homebrew, sudo, and Xcode set up for it | The guest user is `admin`; a separate user would have to redo all of that (and lost Homebrew's trust store) |
| Hosted GitHub macOS runners can't nest VMs | CI lints and validates; images build on a Mac |
| `/workspace` can't be created on macOS's read-only system volume | Claude's `--base-dir` is `~/workspace` |

## 3. Decisions

| Decision | Why |
| --- | --- |
| Runner model only; no Remote Control | Headless, no interactive login, fits dispatch from the vendor's UI |
| Claude only for now | The only runner that gives a clean VM per session without an Enterprise plan; Cursor is documented in [cursor.md](cursor.md) |
| One runner, one simulator per VM | Keeps sizing predictable: ~12 GB per VM |
| The base image tracks the latest, like the Claude CLI | Newest Xcode image for the host's macOS, pulled every build; breakage is fixed as it comes rather than avoided by pinning |
| Ephemeral VMs: one session per clone | A session can `brew install` or decrypt secrets without affecting the next, like a GitHub Actions runner |
| One image; runner chosen at configure time | The Xcode image is ~150 GB; variants would differ by almost nothing |
| Secrets in the host Keychain, pushed over `tart exec` stdin | Never in the repo, the image, or a process list |
| Sessions push their work before an early end | `--push-outcome-on-release` plus a Stop hook in the image's `~/.claude` that asks Claude to commit and push; a fresh VM per session otherwise loses unpushed work |
| Git proxy watchdog, log-only at first | [anthropics/claude-code#96856](https://github.com/anthropics/claude-code/issues/96856) breaks git for the rest of a session; `WATCHDOG_ACTION=terminate` requeues it onto a fresh VM once detection is proven |
| No secrets for sessions | Parity with Anthropic-hosted environments; secret-needing work stays in GitHub Actions ([secrets.md](secrets.md)) |
| The guest user (`admin`) owns Homebrew; runner CLIs are casks | The base image's own setup; sessions can `brew install` what a project needs |
| The runner upgrades its own cask at start | Rebuilds would otherwise be weekly; each `AGENT` upgrades only its own CLI |
| The guest user is root in its VM (the base image's passwordless sudo) | Sessions can run installers that need root, and the host sets the hostname over `tart exec`. SSH with `admin`/`admin` stays on, reachable only from the host through Tart's NAT. The blast radius is one session's throwaway VM |
| Host scripts act in the guest as the guest user | `tart exec` runs in the logged-in GUI session, which automatic login makes `admin`'s; only the hostname needs `sudo` |
| File ownership isn't an isolation boundary | Sessions run as `admin`, which owns its home and Homebrew and has sudo; the fresh clone per session is the reset |
| Claude's orchestrator starts VMs, not our own loop | The environment secret stays on the host; VMs boot per session plus `RUNNER_MIN_IDLE` standby; the hook is ~100 lines of shell |
| Runners start at host login | Hosts are Macs with a display. After a restart, either log in each time (FileVault on) or use automatic login (FileVault off); macOS allows automatic login only without FileVault |

## 4. How a runner runs

On the host, `scripts/orchestrator-run.sh` (a LaunchAgent via `make runner-install`)
puts the Keychain's environment secret in `SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET` and
execs `claude self-hosted-runner orchestrator --hooks-dir hooks --min-idle
$RUNNER_MIN_IDLE --hook-concurrency 1 --expected-spawn-seconds 300`. For each queued
session, and each missing standby runner, the orchestrator runs `hooks/spawn-runner`:

1. Exit 0 if this `CLAUDE_RUNNER_ORDER_ID` already has a VM (redelivery).
2. Claim the first free `runner-1..runner-$RUNNER_MAX` with `mkdir build/runners/<vm>`;
   none free exits 1, so the session waits in Anthropic's queue and is re-offered.
3. Copy the work order (mode 600), submit `scripts/runner-once.sh <vm>` as launchd job
   `com.agent-images.<vm>`, and return. Any unexpected failure releases the claim and
   exits 1; exit 2 (don't retry) is only for a missing `vms/runner.env`.

`runner-once.sh` clones the image, starts it, runs `EPHEMERAL=true
ENVIRONMENT_SECRET_FILE=<work order> vm-configure --config vms/runner.env <vm>`, deletes
the work order, streams the guest's runner and watchdog logs to
`build/logs/<vm>.guest.{out,err}` (they'd otherwise die with the VM), waits for the guest to
power off, then deletes the VM and the claim.
SIGTERM (`runner-stop`, `runner-uninstall`) does the same early. At startup the
orchestrator script reclaims claims whose job died (a host crash).

The environment secret never enters a VM. Each VM registers with a single-use work
order that's spent at registration, so a session that reads it gets nothing usable.

In the guest:

1. Boot → `admin` logs in automatically → LaunchAgent `com.agent-images.runner` loads.
2. launchd runs `~/bin/agent-runner` through `zsh -l`, so `PATH` comes from
   `~/.zprofile` (`brew shellenv`, then `~/bin`). It reads `~/.config/agent-runner/runner.env`. If it's
   missing, it exits and launchd waits for the file (`KeepAlive` / `PathState`).
3. It execs `~/bin/agent-runner-<AGENT>`, which runs `brew upgrade --cask` for its
   own CLI (`claude-code@latest`), makes sure the user has an iPhone simulator
   (`agent-ensure-simulator`; devices are per user, and this is the first moment the
   GUI session exists), and then starts the runner.
4. The runner exits (Claude exits after every session by default). With
   `EPHEMERAL=true`, `agent-runner` then runs `sudo -n /sbin/shutdown -h now` (the
   base image gives `admin` passwordless sudo). Without it (a persistent
   VM from `make vm-up`), launchd restarts the runner, throttled to one start per 30 s.

Alongside, LaunchAgent `com.agent-images.watchdog` runs `~/bin/agent-runner-watchdog`
every 30 s. It reads each session's git proxy port from
`~/workspace/_sessions/<id>.gitconfig`; two refused connections in a row mean the relay
is gone. It logs to `~/Library/Logs/agent-runner-watchdog.{out,err}`, and with
`WATCHDOG_ACTION=terminate` sends the runner SIGTERM so the session requeues.

| File in the guest | Written by | Mode |
| --- | --- | --- |
| `~/.config/agent-runner/runner.env` | `vm-configure` from `vms/runner.env` (`vms/<vm>.env` for a debug VM) | 600 |
| `~/.claude-runner/environment-secret` | `vm-configure`: the work order (runner VMs) or the Keychain secret (debug VMs) | 600 |

`runner.env` is written last, so launchd never starts a runner with missing secrets.

## 5. Runners

**Claude**: `claude self-hosted-runner --environment-secret-file … --client-label
<host>-<vm> --base-dir ~/workspace --capacity 1 --remove-session-state
--confine-repo-settings enforce`, plus by default `--use-anthropic-git-proxy`
(Anthropic-managed git auth; it replaces the guest user's `~/.gitconfig`; fine, since each VM serves one session),
`--configure-git` (git identity and Anthropic commit signing; the image has none),
`--push-outcome-on-release` only without the git proxy (its release-time push needs git
credentials on the VM, and the proxy setup has none; the Stop hook covers that case), `--release-idle-session-min 60` (an idle session gives
its VM back and resumes on a fresh one), and `--kill-session-after-min 480`; and
`--lock-to-account` if set. Each is a setting in `vms/runner.env`. The label, which is
also the guest's hostname, is `RUNNER_LABEL_PREFIX` (default the host's
`LocalHostName`) plus the VM name. `--environment-secret-file` holds the
orchestrator's work order on runner VMs, the environment secret on debug VMs.
`/healthz` on port 8080 in the guest. The LaunchAgent writes the runner's stdout to
`~/Library/Logs/agent-runner.out` and stderr to `.err`, and gives it 120 s to drain on
stop; in ephemeral mode `agent-runner` forwards SIGTERM to it.

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

## 7. Open items

Verified on the first builds and boots (2026-09-29, Xcode 27.0 base image): `brew
bundle` including casks; automatic login, which also decides who `tart exec` runs as
(so the guest user is the base image's `admin`); `ensure-xcode.sh` end to end
(`automationmodetool` prints a root password prompt but succeeds); `simctl` at build
time (5 iPhone simulators); the runner starting with every flag, including bare
`--remove-session-state`, and asking for a stop budget of 110 s (the plist gives 120 s).

Verified on the first real session (2026-09-29, `make runner-run` on a MacBook): the
orchestrator reads the environment ID from the environment key (a key cut off at 128
characters fails; `make secret-set` now stores keys whole); a standby VM registered
with a work order 43 s after the spawn request and picked up the next session; the
orchestrator spawned a new standby as soon as it did; the repo cloned through the git
proxy; the Stop hook reached the session (2 files seeded from `~/.claude`); the
watchdog's inputs exist in a live session (`_sessions/<id>.gitconfig` naming a live
relay port) and the `pkill` pattern matches; deleting the session completed it, and
the runner deregistered, the VM powered off and was deleted. Tart before 2.39 can't
`tart get`/`list` a running VM with an ASIF disk (openai/tart#1344), so `vm_exists`
checks Tart's VM directory instead.

Verified on a second host (hangar): `make runner-install` runs the orchestrator as a
LaunchAgent, and its `spawn-runner` hook submits runner VM jobs from there.

Still to check:

| Item | Why |
| --- | --- |
| macOS UI tests run without prompts (automation mode, `_developer`) | Checked at build: automation mode needs no authentication, the user is in `_developer`, developer mode is on; a real UI test hasn't run yet |
| The watchdog acts on a real broken relay (`WATCHDOG_ACTION=terminate`) and the session resumes on the standby | Detection and the `pkill` pattern are verified; a real #96856 failure hasn't happened yet |
| The Stop hook's nudge actually gets Claude to push before a turn ends | The hook reaches sessions; its effect on a session with unpushed work is untested |
| Idle release after `CLAUDE_RELEASE_IDLE_SESSION_MIN` resumes the session on a fresh VM | The test session was deleted rather than left idle |

