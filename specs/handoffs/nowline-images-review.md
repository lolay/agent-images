Adds [nowline](https://github.com/lolay/nowline) to the macOS and Linux runner images, so sessions can parse, validate and convert `.nowline` roadmaps. The Linux image now gets nowline and triage from `lolay/tap` through Homebrew, as macOS does.

## Review guide

Review tier: [study], because the Linux build now runs Homebrew's install script (curl | bash) and installs a prebuilt tap, outside Ubuntu's archive.
Blast radius: every Linux runner session, since it's baked into the image; a revert plus `make build` removes it. macOS is one Brewfile line from an already-trusted tap.
Review time: ~8 min

1. [study] `images/linux/scripts/install-packages.sh`: installs Homebrew, trusts the tap, runs `brew bundle`
2. [check] `images/linux/Brewfile`, `images/linux/packages.txt`, `scripts/linux/build-image.sh`, `images/linux/files/profile.sh`: the bundle, brew's `file` and `procps` prerequisites, staging the Brewfile into the VM, brew's bin on PATH (last, so it never shadows apt tools)
3. [check] `images/macos/Brewfile`: `brew "lolay/tap/nowline"`
4. [skim] `README.md`: package rows

## Validation

- `shellcheck`, `shfmt`: not run, not installed in this session
- `nowline` and `triage` formulae: both have `on_linux` x86_64 URLs in `lolay/homebrew-tap`
- `bash -n` on `install-packages.sh`: pass
- `make build` (macOS, Linux): not run, needs the build hosts

## Rollback

Revert the squash commit and rebuild the images. Running VMs keep their copy-on-write clone until the next build.

<details open>
<summary>[study] Linux install path</summary>

| | Before | After |
| --- | --- | --- |
| Package managers | apt | apt, plus Homebrew in `/home/linuxbrew/.linuxbrew` |
| Tap formulae | none | `lolay/tap/nowline`, `lolay/tap/triage`; tap trusted first |
| PATH | apt, `~/.local/bin`, Android | same, brew's bin appended last |
| Fetched unverified | Claude installer | also Homebrew's `install.sh` from `Homebrew/install@HEAD` |

Formula downloads are sha256-pinned by the tap, which answers the earlier checksum question. Homebrew's installer itself is `curl | bash` from HEAD, the same trust as the Claude installer already there. Brew is not on non-login PATH for non-bash shells, which `bash -l` startup covers.

</details>

<details>
<summary>[check] macOS Brewfile</summary>

One line next to `lolay/tap/triage`. `install-packages.sh` already trusts the tap before the bundle runs.

</details>

<details>
<summary>[skim] README</summary>

Version rows only.

</details>

## Decisions

- Linux uses Homebrew for tap formulae so one Brewfile shape serves both images; apt stays for everything else.
- No compiler: the formulae are prebuilt, so only `file` and `procps` are added.
- This branch carries `specs/handoffs/nowline-images-review.md`; delete it before merge.

Assisted-by: Claude Code
