Compares the `linux` runner type with Anthropic's default cloud environment
(hardware, cold start with and without our tools, cost, tool versions, proxy,
credentials), and adds `scripts/anthropic/setup.sh`, which gives an Anthropic
environment `agent-linux`'s toolsets so we can measure whether Cloud Run's compute
is worth paying for. Spec: `specs/anthropic-cloud.md`.

## Review guide

Review tier: [study], because `setup.sh` grants `ubuntu` passwordless sudo in the environments that run it.
Blast radius: only Anthropic environments that someone points at the script; nothing deploys (no `images/`, `infra/gcp/` or `scripts/cloud/` changes). Revert is safe.
Review time: ~10 min

1. [study] [`scripts/anthropic/setup.sh`](https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-81c438a0571c4fcec41494b81cdea7c33c447657f2e12bdb36bb0ee6d14a8809): installs the toolsets as `ubuntu`, sudoers drop-in, `brew` wrapper, PATH
2. [check] [`specs/anthropic-cloud.md`](https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-b0f56089017ac4cd60f50524cc3a0b5b54a604fdcc7a447748b9491c1d1e970b): the comparison, the timings, the report prompt
3. [skim] [`README.md`](https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-b335630551682c19a781afebcf4d07bf978fb1f8ac04c6bf87428ed5106870f5): types, images and where they run
4. [skim] [`specs/secrets.md`](https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-9c5706b875aa440429e68b5c580ffbbe08381a0303db50aeb40f09f5ef9b6bb6), [`specs/linux.md`](https://github.com/lolay/agent-images/compare/main...claude/eloquent-cerf-s5e8go#diff-e33a8b348d6dd828bf3f9e4ceede223a18398718f4d57d03d0a00cbfc2722045): network secrets now reach Team and Enterprise; a link

## Validation

- The ten toolsets installed on Anthropic's image, timed: all ok, 254.9 s, versions checked
- `shellcheck scripts/anthropic/setup.sh`: clean
- `setup.sh` end to end: not run. This session's permission mode blocked its sudoers and profile writes, so the installs ran as the image's `claude` user instead. The report prompt in the spec covers it on a real environment

## Rollback

Revert the squash commit, and remove the setup line from any environment using it.

<details open>
<summary>[study] Privilege and environment changes in setup.sh</summary>

| | Before (Anthropic's image) | After the script |
| --- | --- | --- |
| `ubuntu` sudo | In the `sudo` group, but its password is locked, so no sudo | Passwordless, `/etc/sudoers.d/agent-images` |
| Homebrew | None | `/home/linuxbrew`, owned by `ubuntu`; root runs it through `/opt/agent-images/bin/brew` (`sudo -u ubuntu`) |
| PATH, root shells | Anthropic's runtimes first | Homebrew and rustup first, from `/etc/profile.d/zz-agent-images.sh` and `/root/.bashrc` |
| Proxy variables passed to `ubuntu` | n/a | `HTTPS_PROXY`, `NO_PROXY` and their lowercase forms only; the CA variables point into root-only `/root/.ccr` (curl exit 77) |

Why `ubuntu` and not root: Homebrew refuses root outside a container it recognizes, and Anthropic's VM has no `/.dockerenv`. Sessions there are root already, so the sudoers drop-in widens nothing a session couldn't do.

</details>

<details>
<summary>[check] The comparison's claims</summary>

The ones to check against their sources:

- No separate charge for Anthropic's VM, and setup scripts cached when they finish in about 5 minutes: Anthropic's docs, linked inline.
- Credentials: the five token variables are one placeholder, `proxy-injected`; checked by length and hash, values not printed.
- Versions: Anthropic's from this VM; `agent-linux`'s from installing its toolsets on the same VM today.
- The 12 s plain cold start was measured by hand, not by a session.

</details>

<details>
<summary>Decisions</summary>

- The script reuses `install-toolset.sh` rather than inlining a tool list, so the two can't drift.
- No session-start install: at 255 s the script fits Anthropic's 5-minute cache window, so only a cache build pays for it.

</details>
