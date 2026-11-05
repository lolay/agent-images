# linux runner test, 20261105T152701Z

Session `cse_01BNKwNXiTXuGGS2RujaUTiD`, runner `linux-0792d3a5`, branch
`claude/runner-test-linux-6rrxoo`. Claude Code 2.1.318 on Opus 5.5.

| Check | Result | Value |
| --- | --- | --- |
| Host facts | pass | Ubuntu 24.04.5 LTS, kernel 6.9.12, x86_64, 5 CPUs, 15.6 GiB RAM, no swap, user `ubuntu` (uid 1000) |
| `sudo -n true` refused | pass | exit 1, "no new privileges" flag set |
| Claude Code is latest | pass | 2.1.318 installed, 2.1.318 published |
| `$DISPLAY` | n/a | unset |
| No credential-like env names | pass | flagged names are all exempt (see Notes) |
| Session notice | pass | time line shown and correct; cost line skipped (`AGENT_COST_USD_PER_HOUR` unset) |
| `$AGENT_OUTPUTS` ends in session id | pass | `/mnt/outputs/linux/2026-11-05/cse_01BNKwNXiTXuGGS2RujaUTiD` |
| Storage matches what the session was told | pass | scratch, outputs, datasets and cache paths all as described |
| Scratch is a real disk | pass | `/dev/mapper/md0` ext4, 9.8 GiB (10 GiB volume); 4 GiB write left `free -m` used at 688 MiB (was 695) |
| Scratch throughput (4 GiB, direct I/O) | pass | write 210 MB/s, read 210 MB/s |
| Outputs round-trip (1 GiB) | pass | sha256 match; write 62 MB/s, read 374 MB/s |
| Datasets read-only | pass | empty; `touch` fails with "Read-only file system" |
| Isolation marker absent | pass | `~/.runner-test-marker` not found; created it |
| pypi.org/simple | pass | 200, 530 ms |
| registry.npmjs.org | pass | 200, 67 ms |
| index.crates.io | pass | 200, 65 ms |
| proxy.golang.org | pass | 200, 16 ms |
| ghcr.io/v2 | pass | 401 (auth challenge, answering), 98 ms |
| formulae.brew.sh (HEAD) | pass | 200, 341 ms |
| Ubuntu archive | pass | archive.ubuntu.com 200 (229 ms), security.ubuntu.com 200 (560 ms); `apt-get update` itself fails as non-root (see Notes) |
| Python (uv + pytest) | pass | 1 passed, 3.2 s |
| Node (tsc + `node --test`) | pass | 1 passed, 5.1 s including installing TypeScript |
| Go (table test) | pass | ok, 15.4 s (cold build cache) |
| Rust (`cargo test`) | pass | 1 passed, 6.6 s |
| ripgrep `cargo build --release` | pass | 33.1 s wall, 86.3 s user, 276% CPU, peak RSS 368 MiB |
| ripgrep `cargo test --release` | pass | 12.5 s wall, 446 passed, 0 failed, peak RSS 308 MiB |
| Playwright install `--with-deps` | fail | exit 1: needs `sudo` for apt; without `--with-deps` it installs in 11.0 s |
| Browser screenshot | pass | `browser.png` 1280x720, 966 distinct colors, example.com rendered |
| Headed display screenshot | skipped | `$DISPLAY` unset |
| Git push | pass | pushed in 2.4 s |

## Timings

| Step | Time |
| --- | --- |
| 1. Facts | about 1 s |
| 2. Storage | about 65 s: scratch write 20.5 s, read 20.5 s; outputs 1 GiB write 17.3 s, read 2.9 s |
| 3. Isolation | under 1 s |
| 4. Network | about 4 s (`apt-get update` 1.9 s to fail) |
| 5. Toolchains | about 30 s: Python 3.2 s, Node 5.1 s, Go 15.4 s, Rust 6.6 s |
| 6. A real build | about 50 s: shallow clone 4.9 s, build 33.1 s, test 12.5 s (ripgrep `91f5d22e`) |
| 7. Browser | about 14 s: `--with-deps` 0.8 s (failed), Chromium install 11.0 s, screenshot 1.5 s |
| 8. Git | push 2.4 s |

Slowest: storage (65 s), the ripgrep build and test (50 s), toolchains (30 s, half of it Go's cold cache).

## Missing from the image

Nothing a session would expect preinstalled. Installed along the way, all project-local
and as intended: TypeScript 7.0.2 and `@types/node` (pnpm dev dependencies, 2.2 s),
Playwright and its Chromium headless shell (into `/scratch/cache/ms-playwright`, 11 s),
and Pillow through `uv run --with` to count screenshot colors.

## Notes

- **CPU count.** `nproc` reports 5, while specs/gcp.md gives the `linux` type 4 vCPU.
  Either the job is configured with more than the spec says or Cloud Run exposes an
  extra CPU; the ripgrep build used 276% CPU, so the build didn't saturate it anyway.
  Worth reconciling before this run becomes the baseline.
- **Scratch throughput** came out at exactly 210 MB/s in both directions, which looks
  like a provisioned throughput cap rather than the disk's limit.
- **Outputs read** at 374 MB/s is probably the page cache serving a file just written
  through the same FUSE mount, so it isn't a fair read figure for the bucket.
- **Session notice.** Only the time line appeared: "⚠ This session will time out after
  60 minutes of inactivity, and ends by 3:26 PM PST at the latest (in 8 h 0 min). Push
  your work before then." It came in the SessionStart hook's message and Claude repeated
  it as the first line of its first reply. 60 minutes matches
  `SELF_HOSTED_RUNNER_SESSION_IDLE_MS=3600000`. The session started 07:26:46 PST
  (`CCR_SPAWN_TIMESTAMP_MS`); plus `SELF_HOSTED_RUNNER_MAX_LIFETIME_MS=28800000` (8 h)
  that's 15:26:46 PST, earlier than `AGENT_RETIRE_AT=1793925691` (16:41:31 PST), so
  3:26 PM is right. No cost line: `AGENT_COST_USD_PER_HOUR` and `AGENT_COST_DETAIL` are
  unset. `SELF_HOSTED_RUNNER_IDLE_SHUTDOWN_MS=3600000`, `AGENT_RETIRE_MARGIN_SEC=900`,
  `AGENT_TASK_TIMEOUT_SEC=34200`.
- **Env names flagged by the credential patterns**, all exempt: `CLAUDE_CODE_MESSAGING_TOKEN`,
  `CLAUDE_CODE_SESSION_ACCESS_TOKEN`, `CLAUDE_SESSION_INGRESS_TOKEN_FILE` (Claude's
  session variables), `CCR_AUTO_MODE_USER_ENV_KEYS_FACT` (CCR), and `GIT_CONFIG_KEY_0`
  to `_2` (the runner's git proxy configuration). The full list of names is in
  `env-names.txt`.
- **Session id** came from `CLAUDE_CODE_REMOTE_SESSION_ID` (`cse_01BNKwNXiTXuGGS2RujaUTiD`);
  `CLAUDE_CODE_SESSION_ID` is `392d3378-da9e-5694-8f8d-4034352f0895`.
- **Storage as described.** `/scratch` is the 10 GiB ext4 volume with 9.2 GiB free at
  start, as the start-of-session facts said; `TMPDIR`, `XDG_CACHE_HOME`, pip, npm, Go,
  Cargo and Homebrew caches all point under `/scratch`. `$HOME` is on the in-memory
  overlay root. Outputs is `lolay-agents-outputs` on gcsfuse (rw), datasets is
  `lolay-agents-datasets` on gcsfuse (ro).
- **Hostname is `localhost`**, so the isolation marker also records the session id
  to tell runs apart: `2026-11-05T15:28:43Z localhost cse_01BNKwNXiTXuGGS2RujaUTiD`.
- **`apt-get update`** can't take its lock as `ubuntu`, which is how the image is meant
  to work (no root). The archive answers over HTTP, so reachability passes.
  `playwright install --with-deps` fails for the same reason; the image already has
  Chromium's libraries, so plain `playwright install chromium` is the command to use
  here, as the image's CLAUDE.md says. The test step might say so too.
- **`npx` after `pnpm init`.** pnpm 12's `pnpm init` writes a `devEngines` field that
  npm 11's `npx` refuses (`EBADDEVENGINES`), so the test's `npx playwright ...` fails in
  a pnpm-initialized project. `pnpm exec` works. Not a runner problem, but it'll bite
  anyone following the test literally.
- **TypeScript 7** requires an explicit `rootDir` when `outDir` is set (TS5011); my first
  tsconfig missed it.
- **Session-start doctor.** The personal plugin's `make doctor` runs this repo's `linux`
  triage profile, which checks the orchestration host (VT-x, LXD, `vms/runner.env`), so
  it fails 9 checks inside every session container. That's noise in a session; the
  doctor could skip host checks when `AGENT_TYPE` is set.
