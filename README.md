# agent-images

A macOS VM image for running coding-agent **self-hosted runners** on Apple Silicon:
Xcode, Simulator, and an auto-login GUI session, so agents can build and run UI tests
on a Mac. Built with Packer and Tart, run from one Makefile.

One image, one runner per VM, one session per VM. The agent is chosen per VM with
`AGENT=` in `vms/<vm>.env`; today that's `claude` (`claude self-hosted-runner`, any
repo a session asks for). Cursor is researched but not implemented
([specs/cursor.md](specs/cursor.md)).

A second image does the same for Android on a small x86_64 Linux box: one LXD VM per
session with the Android SDK and an emulator that runs inside the VM on nested KVM.
Apple Silicon can't run the emulator inside a VM, so Android needs this host; see
[specs/linux.md](specs/linux.md) for why, the design, and hardware to buy. Setup is
[below](#linux-host-android).

Design, constraints, and open items are in [specs/design.md](specs/design.md). Make
targets are documented in [Makefile.md](Makefile.md).

## Requirements

Hosts: Apple Silicon Macs on macOS 27 (Golden Gate). Guests are Golden Gate too.
Up to two macOS VMs run at once per host (Apple's license, enforced by macOS).

| Host role | Needs |
| --- | --- |
| Run host (a Mac with a display, logged in) | `tart`, `make`, `triage`, the macOS Keychain, Claude Code (`claude-code@latest` cask) for the orchestrator |
| Build host | `tart`, `make`, `triage`, `packer`, `python3`, `shellcheck`, `shfmt` |

`make doctor` (run host) and `make doctor MODE=build` check all of this with
[triage](https://github.com/lolay/triage) ([triage.yaml](triage.yaml)) and print the
command that fixes each missing piece.

Versions current at the last lookup, 2026-09-30. Nothing in the image is pinned: it
tracks the latest and pins only on breakage
([specs/design.md](specs/design.md#track-the-latest-pin-only-on-breakage)).

| Component | Version | Where it's set |
| --- | --- | --- |
| Tart | 2.38.0 (2.39+ recommended: fixes `tart list` while runners run) | Host install (`brew install openai/tools/tart`) |
| Packer | 1.16.1 | `required_version` in `images/macos/plugins.pkr.hcl` |
| packer-plugin-tart | 1.21.0 | `~> 1.21` in `images/macos/plugins.pkr.hcl` |
| Base image | `macos-<host codename>-xcode:latest`, today `macos-golden-gate-xcode` | Not pinned: `make build` pulls the newest Xcode image for the host's macOS (`MACOS_CODENAME_<major>` in the Makefile) |
| Claude Code | `claude-code@latest` cask (2.1.286) | `images/macos/Brewfile`; the Claude runner upgrades it at each start |
| Image packages | xcodegen 2.46.0, xcbeautify 3.2.1, swiftlint 0.65.1, swiftformat 0.63.1, sops 3.13.3, age 1.3.2, triage 0.4.0 (`lolay/tap`), asccli 0.18.4 | `images/macos/Brewfile`; current release at build time |
| shellcheck / shfmt | 0.11.0 / 3.14.1 | Build host tools for `make lint` |
| actions/checkout | v7.0.1 | `.github/workflows/ci.yml`, `.github/workflows/linux-image-smoke.yml` |
| Linux host | Ubuntu 24.04 Server, LXD snap `latest/stable` (refreshes held; `make build` refreshes) | `scripts/linux/host-setup.sh` |
| Linux image | `ubuntu:24.04`; OpenJDK 21; Android command-line tools 23.0, emulator 37.1.11, platform and x86_64 Google APIs image `android-37.0`, build-tools 37.0.0 | `images/linux/packages.txt`, `images/linux/scripts/ensure-android-sdk.sh`: newest stable at build time |
| hashicorp/setup-packer | v3.4.0 (installs Packer 1.16.1) | `.github/workflows/ci.yml` |

## Build (build host)

```bash
# Third-party taps must be trusted: naming a formula trusts only that formula,
# and Tart's softnet dependency comes from the same tap.
brew tap openai/tools && brew tap lolay/tap && brew trust openai/tools lolay/tap
brew install openai/tools/tart hashicorp/tap/packer lolay/tap/triage shellcheck shfmt
make init                  # plugin install, creates .env
$EDITOR .env               # optional: REGISTRY for make publish
make doctor MODE=build
make build
make publish CONFIRM_PUBLISH=1   # optional: push to a private registry
```

## Run (run host)

```bash
brew tap openai/tools && brew tap lolay/tap && brew trust openai/tools lolay/tap
brew install openai/tools/tart lolay/tap/triage
brew install --cask claude-code@latest
make init && $EDITOR .env            # IMAGE_REF, RUNNER_MAX, RUNNER_MIN_IDLE
make doctor                          # triage: what's missing and how to fix it

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

`runner-install` starts the orchestrator whenever the host user logs in. After the
host restarts, pick one:

| Option | After a restart | Use it for |
| --- | --- | --- |
| **Log in each time** (FileVault on) | Runners start once you enter your password at the startup screen, which unlocks the disk and logs you in. For planned restarts, `sudo fdesetup authrestart` skips that once | Laptops, or any Mac that isn't physically secure |
| **Automatic login** (FileVault off) | Runners come back on their own, even after a power cut. Turn FileVault off (System Settings → Privacy & Security → FileVault, or `sudo fdesetup disable`), then set System Settings → Users & Groups → Automatically log in as | A dedicated Mac in a locked space |

macOS won't turn on automatic login while FileVault is on. Either way, the orchestrator
runs as whoever logs in, so a dedicated runner account must be the one that logs in.
This is only about the host: VM disks are separate and always boot straight to the
base image's `admin` user.

Runner VMs outlive an orchestrator restart; `make runner-stop` deletes them (their sessions requeue), and
`make runner-uninstall` stops everything (see [Stop, rebuild, restart](#stop-rebuild-restart)).
`make vm-create` / `vm-up` still give you a persistent VM for debugging, under any
name except `runner-N`.

## Linux host (Android)

Hardware, the reasons for it, and open items are in [specs/linux.md](specs/linux.md).
Any x86_64 box with VT-x or AMD-V and 64 GB (two sessions) works; the pick there is an
ASUS NUC 15 Pro.

```bash
# Ubuntu 24.04 Server, virtualization on in the firmware, this repo cloned.
make host-setup            # KVM nesting, LXD with a ZFS pool, Claude Code, lingering
sudo reboot                # picks up the lxd group and lingering
make init                  # .env from .env.linux.example
make doctor
make build                 # the agent-linux LXD image (SDK, emulator snapshot)

# A separate self-hosted environment in claude.ai for this box, so sessions choose
# it for Android work. Its secret stays in ~/.config/agent-images (mode 600).
make secret-set NAME=claude-environment-secret   # prompts; or pipe it in
cp vms/example-linux.env vms/runner.env
make runner-run            # try it in the foreground
make runner-install        # systemd user unit; starts at boot, no login needed
make runner-status
```

Every session gets a fresh VM from `agent-linux`, with the emulator booted as
`emulator-5554` from a clean snapshot. The VM powers off after its session and LXD
deletes it. Up to `RUNNER_MAX` (default 2) VMs, each `VM_CPU` (6) and `VM_MEMORY_GB`
(24).

## Stop, rebuild, restart

The same targets work on both host types. Claude Code upgrades itself at every VM
start, so a rebuild is for everything else: the base image, Homebrew packages, the
Android SDK. VMs already running, standby ones included, keep the image they were cloned
from, so a rebuild reaches a runner only after its VM is replaced.

To stop the runners and keep them stopped, take down the orchestrator along with the
VMs:

```bash
make runner-uninstall      # orchestrator and every runner VM; sessions on them requeue
```

`runner-stop` alone deletes the VMs, but a running orchestrator boots replacements
right away (`RUNNER_MIN_IDLE` standby), so use it to recycle VMs, not to stop. If you
started with `make runner-run`, Ctrl-C stops the orchestrator and leaves the VMs; run
`make runner-stop` to delete them.

To rebuild and restart with the runners down:

```bash
make runner-uninstall
make build                 # pulls the newest base image and current packages
make runner-install        # or make runner-run
make runner-status         # orchestrator healthz, then each VM
```

The runners need to be down for the build when the host is full. On a Mac the build VM
counts toward the two-VM limit, so with both slots busy it can't start. On the Linux box
the build VM wants `BUILD_MEMORY_GB` (16) on top of the runners' 24 GB each, which
leaves a 64 GB host no headroom.

To roll the new image out without a gap in service (a host with room for the build VM,
or a separate build host), build while the runners work and recycle the VMs after:

```bash
make build                 # the new image replaces agent-macos / agent-linux only once finished
make runner-stop           # the running orchestrator boots replacements from the new image
```

`runner-stop` requeues any session in progress, so run it between sessions if you can.
If a separate host builds and publishes the image, run `make image-pull` on each run
host, with `IMAGE_REF` in `.env` pointing at the new tag, before `make runner-stop`.
