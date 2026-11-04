# Claude Code releases and image freshness

Status: **research and proposal.** Data pulled 2026-11-04: npm publish times for
`@anthropic-ai/claude-code` (82 releases, 2026-08-06 to 2026-11-03), the
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

### Schedules

| Job | When (Pacific) | What |
| --- | --- | --- |
| Full build | 04:00 Tuesday to Saturday | `make build`: newest base image and packages. Every npm release of the previous day is out by 22:00, and nearly every Homebrew bump by about 04:00 |
| Release check | Hourly 08:00 to 22:00, every day | If the published version (cask version on macOS, `downloads.claude.ai` on Linux) is newer than the image's, run a refresh build, then recycle idle standbys |

A refresh build is not a full build. It clones the current image, boots it, upgrades
Claude, shuts it down, and swaps it in, the same `-next` swap `make build` does. That's
minutes, not the full build. On macOS the refresh VM counts toward the two-VM limit;
with both slots busy it skips, and the next hourly check tries again. On GCP it's the
last Dockerfile layer only.

Recycling idle standbys: for each runner VM, read the runner's `/healthz`
(`active_sessions`) and `/metrics` (`claude_code_self_hosted_runner_info{version}`) on
port 8080 in the guest. Stop only those with no active sessions and an older version.
The orchestrator replaces them from the new image. If a session lands between check
and stop, `SIGTERM` drains it and it requeues onto a fresh runner. `--exit-if-unused-min`
on standbys (say 240) stays as the backstop if the check is down.

### Where it runs: GitHub Actions on the build host

Hosted runners can't build these images (no nested virtualization for Tart, no LXD),
so the jobs run on a **self-hosted GitHub Actions runner on each build host**, labeled
for that host (`agent-images-macos`, `agent-images-linux`). That also puts every build's
log on GitHub, which is the point.

- Scheduled workflows take a `timezone` since March 2026, so the schedules read as
  written: `cron: '17 8-22 * * *'` and `cron: '7 4 * * 2-6'` with
  `timezone: America/Los_Angeles`. Off the hour on purpose; GitHub delays runs at :00.
- One `concurrency` group per host, so a refresh never overlaps a full build.
- State between runs: none needed in Actions. The job runs on the host, and the host
  knows what it built: the build writes `build/images/<image>.json` (Claude version,
  base image digest, build time, run URL), and the check compares against that.
  If a check ever runs on a hosted runner instead, `actions/cache` with a
  `lookup-only` restore on a key like `claude-2.1.318` works as a "seen this version"
  flag; repository variables work too but need a token with variables write, which
  `GITHUB_TOKEN` doesn't have.
- **This repo is public**, and a self-hosted runner on a public repo will run whatever
  a fork's pull request puts in a workflow file, unless someone approves the run.
  Before registering one: workflows that target these labels trigger only on
  `schedule` and `workflow_dispatch`; set "Require approval for all external
  contributors" for fork pull request workflows; run the Actions runner as its own
  macOS/Linux user without access to the Keychain items or `~/.config/agent-images`.
  Or make the repo private, which removes the problem.

### Keep one image, keep the logs

- **Images: keep the last one.** `make build` already deletes the old `agent-macos`
  before renaming the new one, and `build-image.sh` deletes the old LXD image after
  moving the alias; the refresh build does the same. Add `make image-prune` after each
  full build so Tart's cache doesn't hold old Xcode base images. On GCP, an Artifact
  Registry cleanup policy keeping the most recent image. Keeping one means rollback
  is a rebuild; keeping two would make it a swap.
- **Logs: on GitHub.** The run log, plus a manifest artifact per build: `brew list
  --versions`, `brew list --cask --versions`, `xcodebuild -version` and runtimes (or
  `dpkg-query -W` and the Android SDK list on Linux), `claude --version`, the base image
  digest. Also written to the job summary for a quick look. Artifacts and logs keep
  for 90 days (the maximum for a public repo), which covers far more than the one image
  we keep. Logs of a public repo are public; builds hold no secrets, so that's fine.

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
