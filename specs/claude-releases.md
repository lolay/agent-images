# Claude Code releases and runner freshness

Status: **research and proposal.** Data pulled 2026-11-04: npm publish times for
`@anthropic-ai/claude-code` (82 releases, 2026-08-06 to 2026-11-03) and the
`claude-code@latest` bumps merged into Homebrew/homebrew-cask over the same window.

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
(plain text, the version number), which moves when npm does.

## What actually goes stale

Rebuilding the image isn't what keeps Claude current. Every runner upgrades Claude at
start (`images/shared/runners/claude.sh`: `brew upgrade --cask claude-code@latest` on
macOS, `claude update` on Linux), and every session gets a fresh runner. Two things
still lag:

- **Standby runners.** A standby (`RUNNER_MIN_IDLE`) upgrades when it boots, then waits.
  One booted at 09:00 still serves a 15:00 session on the version that was current at
  09:00, though a release came out at 11:00.
- **macOS lags Homebrew.** The cask trails npm by about 4 hours, and up to 18. That's
  the price of installing through Homebrew.

The nightly image rebuild is for everything else in the image (Xcode base, Brewfile,
apt packages). It only saves each VM the download of a newer Claude.

## Proposal

**1. Rebuild nightly at 04:00 Pacific, Tuesday through Saturday.** Every npm release of
the previous day is out by 22:00, and nearly every Homebrew bump by about 04:00. It's
also hours before the next day's 10:00 window, so the image is fresh before anything
new can land. Sunday and Monday rebuilds would almost always find nothing new.

**2. Bound standby age with `--exit-if-unused-min`.** No new code: pass it to standby
runners (say 240 minutes) and they exit if no session took them, so the orchestrator
boots a replacement that upgrades at start. A standby is then at most four hours
behind. Cost: a VM boot every four hours per standby.

**3. Then, if that's not tight enough, watch for releases and recycle idle standbys.**
A launchd job (macOS) or systemd timer (Linux) every 15 minutes:

- Read the newest version: `downloads.claude.ai/claude-code-releases/latest` on Linux
  and GCP; the `claude-code@latest` cask version on macOS (`brew info --json=v2 --cask`
  after `brew update`, or `formulae.brew.sh/api/cask/claude-code@latest.json`), since
  that's what a Mac VM will install.
- For each runner VM, read the runner's `/healthz` (`active_sessions`) and `/metrics`
  (`claude_code_self_hosted_runner_info{version}`) on port 8080 in the guest.
- Stop only runners with no active sessions and an older version (`make runner-stop`
  for that one VM). The orchestrator replaces it at once, and the new one upgrades at
  start. If a session lands in the moment between check and stop, `SIGTERM` drains and
  the session requeues onto a fresh runner, which is safe.

No debounce: a hotfix within 6 hours happens 12% of the time, and recycling an idle
VM again is cheap.

Not proposed: building an image per release. It would trigger 0 to 3 builds a day for
a component the runners already upgrade themselves.

## If `latest` bites

Runners track `latest` on purpose ([design.md](design.md#track-the-latest-pin-only-on-breakage)).
If a release breaks sessions (as [anthropics/claude-code#96856](https://github.com/anthropics/claude-code/issues/96856)
broke git), switching the cask to `claude-code` (the `stable` channel) is a one-line
change in the Brewfile and `claude.sh`, and it buys about a week of soak.

## Sources

- npm registry: `https://registry.npmjs.org/@anthropic-ai/claude-code` (`time`, `dist-tags`)
- Homebrew/homebrew-cask commits `bump-claude-code@latest-*` and `bump-claude-code-*`
- [Self-hosted runner reference](https://code.claude.com/docs/en/self-hosted-environments-reference): `/healthz`, `/metrics`, `--exit-if-unused-min`, `--min-idle`
