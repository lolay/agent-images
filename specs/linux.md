# Linux runner (Android)

Status: **implemented, not yet run on hardware.** The image's contents are proven
without a Linux host. The provisioning scripts run clean in an `ubuntu:24.04`
container, and the manual `linux-image-smoke` workflow runs them on a hosted
`ubuntu-24.04` runner. On that runner the emulator is two virtualization levels
deep, the same as inside an LXD runner VM. There, on 2026-09-29:
- the build's cold boot of API 37.0 (Android 17, x86_64) reached `boot_completed` in
  49–94 s and saved the snapshot;
- a session's boot loaded the snapshot in about 3.5 s and was ready in 9–55 s;
- KVM reported usable;
- the scripts, run as the hosted image's own user, wrote nothing to stderr.

The LXD side waits for a box; see [Open items](#open-items).

Researched 2026-09-29.

## Why a Linux host

Agents doing Android work need the emulator in their own loop, the way an engineer
runs it locally, not only in CI or a device farm. The emulator needs hardware
virtualization (KVM on Linux, Hypervisor.framework on macOS), which rules out most of
where the agents already run:

| Where | Emulator? | Why |
| --- | --- | --- |
| Anthropic-hosted cloud environments | No | Firecracker VMs with no `/dev/kvm`; the base image can't be replaced, only added to with a setup script. Builds, unit tests, and Robolectric work there |
| This repo's macOS VMs (any Apple Silicon) | No | Apple's nested virtualization is for Linux guests only (M3+ and macOS 15+); macOS guests get `HV_UNSUPPORTED` |
| A Linux VM on an M3+ Mac (`tart run --nested`) | Not supported | KVM works, but Google ships no Linux arm64 emulator or `aapt2`; only community builds |
| The emulator on the Mac host, relayed into the VM over adb | Yes, but outside the session's VM | MacStadium documents it; rejected so the emulator stays inside the session's own VM |
| OrbStack / Docker Desktop on a Mac | No | No nested KVM on any Apple chip; Linux containers only, so no replacement for Tart either |
| Azure Container Instances / Container Apps | No | No privileged or host access |
| Azure VMs (Dsv5, Dsv6, Dasv6, …), EC2 C8i/M8i nested | Yes, one level | A runner VM inside them would put the emulator two levels deep; only `.metal` instances allow it |
| **A small x86_64 Linux box** | **Yes** | Bare metal: KVM for the runner VM, nested KVM for the emulator inside it |

Industry practice for emulator tests in CI is the same hardware answer: Linux with
KVM (GitHub's `ubuntu-latest` runners since 2024, CircleCI's Android image,
reactivecircus/android-emulator-runner), or a hosted device service (Firebase Test Lab,
emulator.wtf, Genymotion).

## Design

The same model as the Mac runner: one image, one ephemeral VM per session, the base
image's own user in each VM, Claude's orchestrator on the host. The emulator runs inside the
session's VM with nested KVM, so the session owns it: restarts, other API levels,
Gradle Managed Devices.

**LXD** (Canonical's VM manager, the snap on Ubuntu) plays Tart's part:

| Tart (macOS) | LXD (Linux) |
| --- | --- |
| `tart clone` + `tart run` | `lxc launch agent-linux runner-N --vm --ephemeral`: a copy-on-write clone on a ZFS pool, removed when it powers off |
| `tart exec -i` | `lxc exec --force-noninteractive` (lxd-agent) |
| `tart set --cpu --memory` | `--config limits.cpu --config limits.memory` |
| Packer build, rename `-next` | `build-image.sh`, `lxc publish` as `agent-linux-next`, move the alias |
| Host LaunchAgents (login) | systemd user units with lingering (boot, no login) |
| Keychain | `~/.config/agent-images/` (mode 600) on a dedicated host |

LXD passes the host CPU through, so VMs get `vmx`/`svm` and `/dev/kvm` without a
config key. Incus (the community fork, same CLI) would work too; LXD is Ubuntu's
first-party option, and Ubuntu's own Incus package is stale. Plain libvirt would mean
writing the exec channel, image publishing, and ephemeral VMs ourselves.

### Guest

`images/linux`, on Ubuntu's official `ubuntu:24.04` VM image (it has lxd-agent):

- **Packages:** `packages.txt` from Ubuntu's archive, unpinned: the Brewfile's session
  tools, OpenJDK 21, and the emulator's libraries.
- **Claude Code:** the native installer; the runner runs `claude update` at start.
- **Android SDK** in `/opt/android-sdk`, owned by the guest user:
  - installed through Google's Android CLI (`android sdk`, which replaced sdkmanager in
    2026): the newest stable API level with an x86_64 Google APIs image (37.0 today)
    and the newest build tools;
  - `android init` installs Google's `android-cli` agent skill into `~/.claude/skills`,
    which the runner seeds into sessions;
  - AVD `agent` (Pixel 8 profile, 4 GB, the minimum from API 37), booted once at build
    time to save a quickboot snapshot.
- **Emulator:** `agent-emulator start|wait|stop|status`. The runner starts it at every
  start, before it registers, so a standby VM has it booted when a session lands. Every
  boot is from the snapshot and saves nothing (`-no-snapshot-save`).
  `-crash-report-mode disabled` is required: nested, a cold boot stalls a vCPU thread
  for over 15 s, and the crash reporter's hang detector then kills the emulator (on
  hosted runners every variant without it died at about 40 s, and every one with it
  booted). `-no-metrics` heads off the metrics notice Google says will become a
  blocking prompt. `AGENT_EMULATOR_ARGS` adds flags.
- **Runner files** are shared with the macOS image (`images/shared`): `agent-runner`,
  the Claude runner script, the git proxy watchdog, and the Stop hook and settings.
  systemd stands in for launchd: `agent-runner.path` starts the runner when
  `runner.env` appears, and `agent-runner-watchdog.timer` runs the watchdog every 30 s.
- **User:** the cloud image's own `ubuntu` (`GUEST_USER`), the counterpart of the
  macOS image's `admin`: cloud-init creates it with passwordless sudo, so it's root in
  its VM and sessions can `sudo apt-get install` what a project needs. `setup-user.sh`
  only adds it to `kvm` and installs agent-images' files, and fails the build if a new
  base image stops giving it passwordless sudo. The provisioners run as that user and
  use `sudo` for the root parts, and host scripts act in the guest as that user
  (`guest_exec`), as on macOS. A separate user would redo what cloud-init sets up and
  fight it in every clone.
- `~/.claude/CLAUDE.md` tells sessions about the emulator and the SDK.

### Host

`scripts/linux` and `hooks/linux` are the Linux counterparts of the macOS scripts,
with the same contracts: `spawn-runner` claims `runner-N` with `mkdir`, copies the
work order, and submits `runner-once.sh` as `agent-images-runner-N.service` with
`systemd-run --user`; `runner-once` launches the ephemeral VM, runs `vm-configure`
(which pushes the work order and writes `runner.env` last), streams the guest's
journal to `build/logs/`, and waits for the VM to delete itself. The shared helpers
come from `scripts/lib.sh`; the host scripts weren't merged into one platform layer
because the macOS path hasn't run on hardware yet either. Once both have, that's a
follow-up.

### Green builds

As on the Mac, a good build writes nothing to stderr: tools that report routine
progress there (`snap refresh`, `cloud-init status`, `adb`'s daemon notices) are sent
to stdout, and real errors keep stderr and fail the build through `set -e`.
`make build` copies the provisioners' stderr to `build/logs/linux-build.err` and ends
with "green" or the number of stderr lines. The `linux-image-smoke` workflow fails
if the image scripts write any stderr.

## Hardware

Per session: 6 vCPU and 24 GB (the emulator's 4 GB, Gradle, Claude, the OS). 64 GB
runs two sessions, like a Mac host; 96 GB runs three. It must be x86_64 (Intel VT-x
or AMD-V).

Prices checked 2026-09-29. DRAM costs more than the box this year; Crucial stopped
consumer sales in February 2026, so retail kits are old stock.

| Part | Pick | Price seen | Where |
| --- | --- | --- | --- |
| Mini PC | **ASUS NUC 15 Pro Tall barebone** RNUC15CRHU70000U: Core Ultra 7 255H (16 cores), 2× SO-DIMM up to 96 GB, M.2 2280 + 2242, 2.5GbE | $662.99 | [Newegg](https://www.newegg.com/asus-nuc-15-pro-barebone-system-intel-core-ultra-7-255h-rnuc15crhu70000u/p/N82E16856110302) · [B&H](https://www.bhphotovideo.com/c/product/1881413-REG/asus_rnuc15crhu70000u_nuc_15_pro_tall.html) · [Best Buy](https://www.bestbuy.com/product/asus-nuc-15-pro-rnuc15crhu70000u-black-barebone-w-intel-ultra-7-255h-intel-arc-140t-graphics/JJGGLHG9L7) · [specs](https://www.asus.com/us/displays-desktops/nucs/nuc-mini-pcs/asus-nuc-15-pro/techspec/) |
| Mini PC, runner-up | ASUS NUC 14 Pro Tall barebone RNUC14RVHU70000UI: Core Ultra 7 155H (16 cores, 22 threads), up to 96 GB | $636–658 | [Newegg](https://www.newegg.com/asus-nuc-14-pro-barebone-intel-core-ultra-7-155h-rnuc14rvhu70000ui/p/N82E16856110260) · [Best Buy](https://www.bestbuy.com/product/asus-nuc-14-pro-barebone-intel-core-ultra-7-155h-triple-storage-thunderbolt-4-wi-fi-6e-bt-5-3/JJGGLQJ97P) |
| RAM | Crucial 64 GB (2×32 GB) DDR5-5600 SO-DIMM CT2K32G56C46S5 | $629.99 | [Best Buy](https://www.bestbuy.com/product/crucial-64gb-kit-2x32gb-ddr5-5600mhz-c46-sodimm-laptop-memory-black/JX8PSKJ67C) · [Newegg](https://www.newegg.com/crucial-64gb-ddr5-5600-cas-latency-cl46-laptop-memory/p/N82E16820156317); 96 GB: CT2K48G56C46S5 ([Amazon](https://www.amazon.com/Crucial-2x48GB-5600MT-5200MT-CT2K48G56C46S5/dp/B0C79K5VGZ)) |
| SSD | WD Black SN7100 2 TB (M.2 2280) | $319.99 | [Newegg](https://www.newegg.com/western-digital-2tb-sn7100-nvme/p/N82E16820250275); alternative [Samsung 990 EVO Plus 2 TB](https://www.amazon.com/Samsung-SSD-Plus-PCIe-2280/dp/B0DHLCRF91) |

About $1,613 a box. Factory-configured from Lenovo, HP, or Dell (all top out at 64 GB,
two sessions, and ship Windows 11 Pro, which gets wiped):

| Box | Config | Price | Where |
| --- | --- | --- | --- |
| **Lenovo ThinkCentre M90q Gen 6 Tiny** 13AC002WUS | Core Ultra 7 265T (20 cores), 64 GB, 2 TB, 1GbE | $1,138.99, manufacturer refurbished, 1-year Lenovo warranty | [refurb](https://buyrefurbished.com/lenovo-thinkcentre-m90q-g6-tiny-pc-intel-ultra-7-265t-64gb-ram-2tb-ssd-w11p-13ac002wus-manufacturer-refurbished/) · [Amazon, new 64 GB/1 TB](https://www.amazon.com/Lenovo-ThinkCentre-Processor-DDR5-5600MT-DisplayPort/dp/B0FXYBFJD2) · [Best Buy](https://www.bestbuy.com/product/lenovo-thinkcentre-m90q-g6-tiny-pc-intel-ultra-7-265t-64gb-ram-2tb-ssd-w11p-black/J39TJQJZ42) · [lenovo.com](https://www.lenovo.com/us/en/p/desktops/thinkcentre/m-series-tiny/thinkcentre-m90q-gen-6-intel-tiny-pc/len102c0065) |
| Lenovo ThinkCentre M75q Gen 5 Tiny | Ryzen 7 PRO 8700GE (8 cores), up to 64 GB, 2× M.2, 1GbE | about $1,050–1,370 with 16 GB | [lenovo.com](https://www.lenovo.com/us/en/p/desktops/thinkcentre/m-series-tiny/lenovo-thinkcentre-m75q-gen-5-tiny-amd/len102c0051) · [CDW](https://www.cdw.com/product/lenovo-thinkcentre-m75q-gen-5-tiny-ryzen-7-pro-8700ge-3.6-ghz-16-gb-s/7969985) |
| HP EliteDesk 8 Mini G1a C3XG5UT#ABA | Ryzen AI 7 350 (8 cores), 64 GB, 1 TB, 1GbE | $2,091–2,145, out of stock when checked | [CDW](https://www.cdw.com/product/hp-elitedesk-8-g1a-desktop-computer-amd-ryzen-ai-7-350-64-gb-1-tb-ssd/8462866) · [Target](https://www.target.com/p/hp-elitedesk-8-g1a-desktop-computer-amd-ryzen-ai-7-350-64-gb-1-tb-ssd-mini-pc-jack-black-amd-chip-windows-11-pro/-/A-1009780256) · [hp.com](https://www.hp.com/us-en/shop/pdp/hp-elitedesk-8-mini-g1a-desktop-pc-customizable-b02q7av-mb) |
| Dell Pro Micro QCM1250 | Core Ultra 7 265T, up to 64 GB, 1GbE | Ready-made configs stop at 32 GB ($2,195); 64 GB via the configurator | [dell.com](https://www.dell.com/en-us/shop/desktop-computers/dell-pro-micro-desktop/spd/dell-pro-qcm1250-micro) |

Best Buy, CDW, lenovo.com, and hp.com wouldn't load during the check, so confirm those
prices. Without a box yet, AWS bare metal (`c7i.metal-24xl`, about $4.28/hr) works
for an afternoon's validation; it has to be `.metal`.

## Setting up a box

1. Firmware: virtualization on (VT-x, or SVM mode on AMD).
2. Install Ubuntu 24.04 Server; clone this repo.
3. `make host-setup`, then reboot (group membership and lingering).
4. `make init` (`.env` from `.env.linux.example`), `make doctor`, `make build`.
5. A self-hosted environment for this box in claude.ai (its own secret, so sessions
   pick "Linux/Android" or the Mac), then `make secret-set NAME=claude-environment-secret`,
   `cp vms/example-linux.env vms/runner.env`, `make runner-run` to try it, and
   `make runner-install`.

## Limits

- Two sessions per 64 GB box, like a Mac host.
- Sessions are root in their VM (passwordless sudo), as on the Mac; the VM is thrown
  away after one session.
- Container isolation isn't used: each session gets a VM, as on the Mac.
- Google's Android CLI collects usage metrics unless called with `--no-metrics`; the
  image build always passes it, and `CLAUDE.md` asks sessions to.

## Open items

Verify on the first box, in this order (the first two are the only untested
assumptions):

| Item | Why |
| --- | --- |
| `lxc launch ubuntu:24.04 t --vm --ephemeral`, then `lxc exec t -- ls /dev/kvm` and `emulator -accel-check` inside | Nested KVM in an LXD VM. The same depth works on hosted runners (Hyper-V underneath); KVM under LXD on bare metal is untested |
| `lxc stop t` deletes it | Ephemeral VMs; documented, not tried |
| `make build` end to end, including the snapshot boot inside the build VM | The snapshot step runs two levels deep |
| A session running `./gradlew connectedDebugAndroidTest` (nowinandroid) | End to end |
| The VM deletes itself after its session; `runner-stop` requeues it | Lifecycle |
| Two VMs at once; the orchestrator back after a reboot | Capacity, lingering |
| `CLAUDE.md` and `skills/android-cli` reach sessions from `~ubuntu/.claude` | The runner seeds settings and hooks; skills and memory are assumed |
| `systemd-run --user` works from the orchestrator's user unit | The hook's job submission |
| triage runs on Linux and the `linux` profile's checks read as intended | Written without a Linux triage run |

## Sources

- [Android Emulator acceleration](https://developer.android.com/studio/run/emulator-acceleration), [emulator release notes](https://developer.android.com/studio/releases/emulator)
- [Apple: nested virtualization](https://developer.apple.com/documentation/virtualization/vzgenericplatformconfiguration/isnestedvirtualizationsupported), [Tart FAQ](https://tart.run/faq/), [OrbStack machines](https://docs.orbstack.dev/machines/)
- [Claude Code cloud environments](https://code.claude.com/docs/en/cloud-environments), [self-hosted environments](https://code.claude.com/docs/en/self-hosted-environments)
- [GitHub: hardware-accelerated Android virtualization](https://github.blog/changelog/2024-04-02-github-actions-hardware-accelerated-android-virtualization-now-available/), [reactivecircus/android-emulator-runner](https://github.com/ReactiveCircus/android-emulator-runner)
- [LXD nested virtualization](https://discuss.linuxcontainers.org/t/lxd-vm-nested-virtualization/14615), [LXD image servers](https://canonical.com/lxd/docs/latest/reference/remote_image_servers/), [kernel: running nested guests](https://docs.kernel.org/virt/kvm/x86/running-nested-guests.html)
- [MacStadium: Android virtual devices](https://docs.macstadium.com/remote-desktop-vdi/configuration/android-virtual-devices)
- [AWS nested virtualization](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html), [Azure Dsv5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dsv5-series)
