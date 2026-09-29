# agent-images

A macOS VM image for running coding-agent **self-hosted runners** on Apple Silicon:
Xcode, Simulator, and an auto-login GUI session, so agents can build and run UI tests
on a Mac. Built with Packer and Tart, run from one Makefile.

One image, one runner per VM, one session per VM. The agent is chosen per VM with
`AGENT=` in `vms/<vm>.env`; today that's `claude` (`claude self-hosted-runner`, any
repo a session asks for). Cursor is researched but not implemented
([specs/cursor.md](specs/cursor.md)).

Design, constraints, and open items are in [specs/design.md](specs/design.md). Make
targets are documented in [Makefile.md](Makefile.md).

## Requirements

Hosts: Apple Silicon Macs on macOS 27 (Golden Gate). Guests are Golden Gate too.
Up to two macOS VMs run at once per host (Apple's license, enforced by macOS).

| Host role | Needs |
| --- | --- |
| Run host (a Mac with a display, logged in) | `tart`, `make`, the macOS Keychain, Claude Code (`claude-code@latest` cask) for the orchestrator |
| Build host | the above plus `packer`, `python3`, `shellcheck`, `shfmt` |

Version baseline, looked up 2026-09-28:

| Component | Version | Where it's pinned |
| --- | --- | --- |
| Tart | 2.38.0 | Host install (`brew install openai/tools/tart`) |
| Packer | 1.16.1 | `required_version` in `images/macos/plugins.pkr.hcl` |
| packer-plugin-tart | 1.21.0 | `~> 1.21` in `images/macos/plugins.pkr.hcl` |
| Base image | `macos-golden-gate-xcode:latest` | `base_image` in `images/macos/variables.pkr.hcl` |
| Claude Code | `claude-code@latest` cask (2.1.284) | `images/macos/Brewfile`; the Claude runner upgrades it at each start |
| Image packages | xcodegen 2.46.0, xcbeautify 3.2.1, swiftlint 0.65.1, swiftformat 0.63.0, sops 3.13.3, age 1.3.2, triage 0.4.0 (`lolay/tap`), asccli 0.18.4 | `images/macos/Brewfile`; current release at build time |
| shellcheck / shfmt | 0.11.0 / 3.14.1 | Build host tools for `make lint` |
| actions/checkout | v7.0.1 | `.github/workflows/ci.yml` |
| hashicorp/setup-packer | v3.4.0 (installs Packer 1.16.1) | `.github/workflows/ci.yml` |

## Build (build host)

```bash
brew install openai/tools/tart hashicorp/tap/packer shellcheck shfmt
make init                  # plugin install, creates .env
$EDITOR .env               # PKR_VAR_user_password, REGISTRY
make doctor MODE=build
make build
make publish CONFIRM_PUBLISH=1   # optional: push to a private registry
```

## Run (run host)

```bash
brew install openai/tools/tart
brew install --cask claude-code@latest
make init && $EDITOR .env            # IMAGE_REF, RUNNER_MAX, RUNNER_MIN_IDLE

# The environment key from claude.ai's Cloud environments page. It stays in the
# host Keychain; VMs only ever get a single-use work order.
make secret-set NAME=claude-environment-secret

cp vms/example-claude.env vms/runner.env              # non-secret settings for every runner VM
make runner-run                                       # try it in the foreground
make runner-install                                   # keep it running as a host LaunchAgent
make runner-status
```

Every session gets a fresh VM, like a GitHub Actions runner. Claude's orchestrator
(`claude self-hosted-runner orchestrator`) runs on the host and calls
[`hooks/spawn-runner`](hooks/spawn-runner) whenever a session is queued, and to keep
`RUNNER_MIN_IDLE` (default 1) standby VMs booted and registered so a new session starts
at once. Each VM clones the image, registers with a single-use work order, serves one
session, powers off, and is deleted. A session can `brew install` whatever its repo
needs without affecting the next one. Up to `RUNNER_MAX` (default 2, Apple's limit)
VMs, named `runner-1`, `runner-2`; each needs `VM_MEMORY_GB` (default 12) free.

`runner-install` starts the orchestrator whenever the host user logs in; turn on
automatic login for that user so runners come back after a restart. Runner VMs outlive
an orchestrator restart; `make runner-stop` deletes them (their sessions requeue), and
`make runner-uninstall` stops everything. `make vm-create` / `vm-up` still give you a
persistent VM for debugging, under any name except `runner-N`.
