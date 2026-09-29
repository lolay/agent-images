# agent-images

A macOS VM image for running coding-agent **self-hosted runners** on Apple Silicon:
Xcode, Simulator, and an auto-login GUI session, so agents can build and run UI tests
on a Mac. Built with Packer and Tart, run from one Makefile.

One image, one runner per VM. The runner is chosen when you configure the VM:

| Runner | Command | Serves |
| --- | --- | --- |
| `claude` | `claude self-hosted-runner` | Any repo a session asks for |
| `cursor` | `agent worker` (My Machines or a team pool) | One repo (use a workspace repo for multi-repo projects) |

Design, constraints, and open items are in [specs/design.md](specs/design.md). Make
targets are documented in [Makefile.md](Makefile.md).

## Requirements

Hosts: Apple Silicon Macs on macOS 27 (Golden Gate). Guests are Golden Gate too.
Up to two macOS VMs run at once per host (Apple's license, enforced by macOS).

| Host role | Needs |
| --- | --- |
| Run host (laptop, Mac mini, EC2 Mac) | `tart`, `make`, the macOS Keychain |
| Build host | the above plus `packer`, `python3`, `shellcheck`, `shfmt` |

Version baseline, looked up 2026-09-28:

| Component | Version | Where it's pinned |
| --- | --- | --- |
| Tart | 2.38.0 | Host install (`brew install openai/tools/tart`) |
| Packer | 1.16.1 | `required_version` in `images/macos/plugins.pkr.hcl` |
| packer-plugin-tart | 1.21.0 | `~> 1.21` in `images/macos/plugins.pkr.hcl` |
| Base image | `macos-golden-gate-xcode:latest` | `base_image` in `images/macos/variables.pkr.hcl` |
| Claude Code | `claude-code@latest` cask (2.1.284) | `images/macos/Brewfile`; the Claude runner upgrades it at each start |
| Cursor CLI | `cursor-cli` cask (2026.09.28-64d2043) | `images/macos/Brewfile`; the Cursor runner upgrades it at each start |
| Image packages | xcodegen 2.46.0, xcbeautify 3.2.1, swiftlint 0.65.1, swiftformat 0.63.0, sops 3.13.3, age 1.3.2, triage 0.4.0 (`lolay/tap`), asccli 0.18.4 | `images/macos/Brewfile`; current release at build time |
| shellcheck / shfmt | 0.11.0 / 3.14.1 | Build host tools for `make lint` |
| actions/checkout | v7.0.1 | `.github/workflows/ci.yml` |

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
make init && $EDITOR .env            # IMAGE_REF: local name or registry ref

# Secrets live in the host Keychain, never in the repo or the image.
make secret-set NAME=claude-environment-secret        # shared by all Claude VMs
make secret-set NAME=cursor-api-key VM=runner-2       # or per VM
make secret-set NAME=git-token VM=runner-2            # Cursor: private or sibling repos

cp vms/example-claude.env vms/runner-1.env            # non-secret settings
make runner-run VM=runner-1                           # try it in the foreground; Ctrl-C stops it
make runner-install VM=runner-1                       # keep it running as a host LaunchAgent
make runner-status VM=runner-1
```

A runner gives every session a fresh VM, like a GitHub Actions runner: it clones the
image, boots it, pushes settings and secrets in, and waits. When the session ends the VM
powers off, and the runner deletes it and clones the next one. A session can
`brew install` whatever its repo needs without affecting the next one. Up to two runners
per host (`runner-1`, `runner-2`); each needs `VM_MEMORY_GB` (default 12) free.

`runner-install` starts the runner whenever the host user logs in. For a host that
should come back on its own after a restart, turn on automatic login for that user. `make vm-create` / `vm-up` still give you a persistent VM for
debugging; don't give it a name a runner uses.
