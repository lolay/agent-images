# Claude Code releases and image freshness

Status: **research.** The automation proposal is in [gcp.md](gcp.md#image-pipeline).
Data pulled 2026-11-04: npm publish times for `@anthropic-ai/claude-code` (82 releases, 2026-08-06 to 2026-11-03), the
`claude-code@latest` bumps merged into Homebrew/homebrew-cask over the same window,
and update timings measured on a Linux container.

## When releases land

| Measure | Value |
| --- | --- |
| Releases | 82 in 90 days: none on 26 days, one on 49, two on 15, three on 1 |
| Days | Weekdays. Saturday 5, Sunday 1 |
| Time (Pacific) | Peak 10:00 to 13:00. 65% out by 14:00, 84% by 17:00, all by 22:00 |
| Gap between releases | Median 23 h, p90 65 h. 12% are followed by another within 6 h (hotfixes) |
| Homebrew `claude-code@latest` lag behind npm | Median 3.9 h, p75 5.7 h, p90 7.3 h, max 17.7 h |
| Versions Homebrew skipped | 6 of 82, when the next release came before the bump |

Channels: `latest` (2.1.318 today) and `stable` (2.1.311). The plain `claude-code`
cask follows `stable`, which lately trails `latest` by about a week. The native
installer and `claude update` read `https://downloads.claude.ai/claude-code-releases/latest`
(plain text, the version number), which moves when npm does. The `claude-code@latest`
cask's version is at `https://formulae.brew.sh/api/cask/claude-code@latest.json`.

## What a stale image costs a cold start

Every runner upgrades Claude at start (`images/shared/runners/claude.sh`), so a stale
image still gives sessions the current Claude, but each VM pays for it on boot.

| Step | Linux, measured | macOS, estimated |
| --- | --- | --- |
| Download | 123 MB (zstd) or 338 MB (raw) | 320 MB (the cask takes the raw binary) |
| Upgrade one version | 5.7 s on a ~110 MB/s datacenter link | Download, plus Homebrew's auto-update, plus Gatekeeper's first look at a new 320 MB binary |
| Already current | 1.4 s (`claude update` checks and exits) | A few seconds: `brew upgrade` still starts Homebrew and may refresh its index |
| Download on a 100 Mbit/s link | about 10 s (zstd), 27 s (raw) | about 26 s |

For scale, the Mac's measured cold start (spawn request to registered runner) was
43 s on 2026-09-29. An upgrade on a home connection plausibly adds 25 to 60% to that.
The macOS numbers are estimates until measured: log a timestamp before and after the
upgrade in `claude.sh`, and `build/logs/runner-N.guest` will show it.

Two smaller wins regardless of the image:

- **Skip the upgrade when current.** Compare `claude --version` with the published
  version (one HTTPS request) and skip `brew upgrade` or `claude update` when they
  match. Saves the no-op cost on every boot once images track releases.
- **Recycle idle standbys after a new image lands.** A standby boots, upgrades, then
  waits; one booted before a release serves the next session on the old version.

## Proposal

The automation is for the GCP images, where builds are plain container builds on
GitHub-hosted runners: an hourly watch from 08:00 to 22:00 Pacific that rebuilds when
Claude or any other dependency moves, a nightly no-cache build at 04:00, one image
kept, and build logs and manifests on GitHub. See
[gcp.md](gcp.md#image-pipeline).

The macOS and Linux hosts stay manual: `make build` when you choose, and Claude
upgrades itself at each VM start as it does today. Two small changes would still help
them:

- **Skip the upgrade when current.** Compare `claude --version` with the published
  version (one HTTPS request) and skip `brew upgrade` or `claude update` when they
  match.
- **Bound standby age** with `--exit-if-unused-min` (say 240) on standby runners, so
  one never serves a session on a version more than four hours old.

## If `latest` bites

Runners track `latest` on purpose ([design.md](design.md#track-the-latest-pin-only-on-breakage)).
If a release breaks sessions (as [anthropics/claude-code#96856](https://github.com/anthropics/claude-code/issues/96856)
broke git), switching to the `claude-code` cask (the `stable` channel) is a one-line
change in the Brewfile and `claude.sh`, and it buys about a week of soak.

## Sources

- npm registry: `https://registry.npmjs.org/@anthropic-ai/claude-code` (`time`, `dist-tags`)
- Homebrew/homebrew-cask commits `bump-claude-code@latest-*` and `bump-claude-code-*`
- [Self-hosted runner reference](https://code.claude.com/docs/en/self-hosted-environments-reference): `/healthz`, `/metrics`, `--exit-if-unused-min`, `--min-idle`
- [GitHub Actions: late March 2026 updates](https://github.blog/changelog/2026-03-26-github-actions-late-march-2026-updates) (schedule `timezone`)
