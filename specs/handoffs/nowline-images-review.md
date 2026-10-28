Adds [nowline](https://github.com/lolay/nowline) to the macOS and Linux runner images, so sessions can parse, validate and convert `.nowline` roadmaps.

## Review guide

Review tier: [study], because the Linux image now downloads an unchecksummed binary into `/usr/local/bin` at build time.
Blast radius: every Linux runner session, since the binary is baked into the image; a revert plus `make build` removes it. macOS is one Brewfile line from an already-trusted tap.
Review time: ~5 min

1. [study] `images/linux/scripts/install-packages.sh`: fetches `nowline-linux-x64` and `nowline.1`/`nowline.5` from `releases/latest`, installs with sudo
2. [check] `images/macos/Brewfile`: `brew "lolay/tap/nowline"`
3. [skim] `README.md`: package rows list nowline 0.11.0

## Validation

- `shellcheck`, `shfmt`: not run, not installed in this session
- Release asset URL: HTTP 200 for `nowline-linux-x64`
- `make build` (macOS, Linux): not run, needs the build hosts

## Rollback

Revert the squash commit and rebuild the images. Running VMs keep their copy-on-write clone until the next build.

<details open>
<summary>[study] Linux download</summary>

| | Before | After |
| --- | --- | --- |
| Source | n/a | `github.com/lolay/nowline/releases/latest/download` |
| Integrity | n/a | none (TLS only) |
| Location | n/a | `/usr/local/bin/nowline`, man pages under `/usr/local/share/man` |
| Check | n/a | `nowline --version` printed in the build log |

The image has no Homebrew, so the tap formula's pinned sha256s aren't available. Decision for you: accept TLS-only trust in a repo we own, or read the checksum from the formula at build time.

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

- Linux uses the GitHub release, not brew: the image has no Homebrew.
- Linux asset is `linux-x64` only; the image is x86_64 because of the emulator.
- This branch carries `specs/handoffs/nowline-images-review.md`; delete it before merge.

Assisted-by: Claude Code
