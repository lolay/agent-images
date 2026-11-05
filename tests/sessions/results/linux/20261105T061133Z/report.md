# linux runner test, 20261105T061133Z

Session `cse_01BNKwNabKkmdBmz9vJa7zka`, host `localhost`, branch `claude/runner-test-linux-9hpni5`.

| Check | Result | Value |
| --- | --- | --- |
| 1. Host | pass | Ubuntu 24.04.5, kernel 6.9.12, x86_64, 5 vCPU, 15 GiB RAM, no swap. `/` overlay 16G, `/scratch` ext4 9.8G, outputs/datasets gcsfuse |
| 1. Non-root user | pass | `ubuntu` (uid 1000) |
| 1. `sudo -n true` | pass (expected to fail) | fails: "no new privileges" flag set |
| 1. Claude Code version | pass | 2.1.318 installed = 2.1.318 published latest |
| 1. `$DISPLAY` | recorded | unset |
| 1. Env var names | fail (pattern match, reviewed) | Names matching `*KEY*`/`*TOKEN*`/`*SECRET*`/`*PASSWORD*`: `GIT_CONFIG_KEY_0..2` and `CCR_AUTO_MODE_USER_ENV_KEYS_FACT` (git config plumbing and a CCR fact name; not credentials, values not inspected). Claude's own: `CLAUDE_CODE_MESSAGING_TOKEN`, `CLAUDE_CODE_SESSION_ACCESS_TOKEN`, `CLAUDE_SESSION_INGRESS_TOKEN_FILE`. Operator runner vars: `SELF_HOSTED_RUNNER_*`. Full name list in `env-names.txt` |
| 1. Session notice, time line | pass | "time out after 60 minutes of inactivity, and ends by 6:11 AM PST at the latest (in 8 h 0 min)". N=60 = `SELF_HOSTED_RUNNER_SESSION_IDLE_MS`=3600000. Session limit from spawn (06:11:22Z) + 8h = 14:11:22Z = 6:11 AM PST, earlier than `AGENT_RETIRE_AT` 15:26:09Z (15:11:09Z with 900 s margin) |
| 1. Session notice, cost line | skipped | `AGENT_COST_USD_PER_HOUR` not set; no cost line appeared. Time line judged alone. Time line came from the SessionStart hook message, not Claude's reply |
| 2. Storage identity | pass | `AGENT_SCRATCH=/scratch`, `AGENT_OUTPUTS` ends in session id, `AGENT_DATASETS=/mnt/datasets`. Session id from `CLAUDE_CODE_REMOTE_SESSION_ID` (`cse_01BN...`); `CLAUDE_CODE_SESSION_ID` is a UUID, different form. Matches CLAUDE.md and start-of-session facts |
| 2. Scratch 4 GiB write (`dd oflag=direct`) | pass | 210 MB/s |
| 2. Scratch 4 GiB read (`iflag=direct`) | pass | 210 MB/s |
| 2. Memory growth during 4 GiB I/O | pass | used 681 → 688 → 680 MB; no 4 GiB growth, so scratch is a real disk |
| 2. Outputs 1 GiB write | pass | `cp` 56.4 MB/s (gcsfuse) |
| 2. Outputs 1 GiB read-back | pass (note) | 1646 MB/s, likely page cache, not a true cold read |
| 2. Outputs checksum | pass | sha256 match (`467adaba...b681b`); probe deleted from outputs |
| 2. Datasets | pass | mounted `ro`, empty; `touch`, write and `mkdir` all fail with EROFS |
| 3. Isolation marker | pass | `$HOME/.runner-test-marker` absent before run; created with timestamp, hostname and session id |
| 4. pypi.org/simple | pass | 200, 0.57 s |
| 4. registry.npmjs.org | pass | 200, 0.08 s |
| 4. index.crates.io | pass | 200, 0.07 s |
| 4. proxy.golang.org | pass | 200, 0.05 s |
| 4. ghcr.io/v2 | pass | 401 anonymous, 0.10 s (reachable; 401 is the expected unauthenticated reply) |
| 4. formulae.brew.sh (HEAD) | pass | 200, 0.06 s |
| 4. Ubuntu archive (`apt-get update`) | pass (note) | fails as default (root-owned lists dir). Passes with user-owned state dirs: 33.7 MB fetched, exit 0. `archive.ubuntu.com` 200 |
| 5. Python (uv, pytest) | pass | `uv sync` + `uv run pytest`: 1 passed |
| 5. Node (tsc, `node --test`) | pass (note) | first build failed with TS2591 (`node:test` not found). Passes after `"types": ["node"]` in tsconfig, with `typescript` 7.0.2 and `@types/node` 26.6.6. Node 26.13.0, npm 11.20.0 |
| 5. Go (table test) | pass | `go test ./...`: 3 subtests pass. Go 1.27.3 |
| 5. Rust (`cargo test`) | pass | 1 unit test pass. cargo/rustc 1.99.1 |
| 6. ripgrep `cargo build --release` | pass | 59.5 s wall, 136 s user, peak RSS 423 MB, 237% CPU |
| 6. ripgrep `cargo test --release` | pass | 114 + 332 tests passed, 0 failed, exit 0. 18.0 s wall, peak RSS 358 MB |
| 7. `playwright install --with-deps chromium` | fail | sudo refused ("no new privileges"); apt deps not installed |
| 7. Playwright chromium (no deps) | pass (note) | downloaded in 17.8 s. Full Chromium still missing libcups, libcairo, libpango; headless shell needs glib, atk, at-spi2 (installed via Homebrew, see Missing) |
| 7. `browser.png` (example.com, headless) | pass | 1024×812 PNG, title "Example Domain", 811 distinct sampled colors |
| 7. Headed display screenshot | skipped | `$DISPLAY` unset |
| 8. Git commit and push | pass | results committed and pushed to `claude/runner-test-linux-9hpni5`; no PR opened |

## Timings

| Step | Wall time | Note |
| --- | --- | --- |
| 5. Homebrew install `uv node go rust gnu-time` | 2m 05s | slowest step |
| 6. ripgrep clone + build + test | 0.9 s + 59.5 s + 18.0 s ≈ 1m 18s | 2nd slowest |
| 2. Storage I/O (dd 4 GiB write + read, 1 GiB outputs write + read) | ≈ 1m 03s | 3rd slowest; dd 20.5 s each way, outputs write 18.1 s |
| 7. Playwright browser download | 17.8 s | `brew install glib atk at-spi2-core` ran unmeasured |
| 5. Python probe (uv sync + pytest) | 4.0 s | |
| 5. Node probe (npm install + tsc + test) | 4.4 s, retry 1.6 s | |
| 5. Go probe | 13.2 s | includes first-run toolchain |
| 5. Rust probe (cargo new + test) | 0.8 s | |
| 4. Network probes | < 1 s each | |
| 4. apt-get update (user dirs) | 3.3 s | root default run 2.3 s, failed |
| 1, 3. Facts and isolation | < 1 min each | not timed individually |

## Missing from the image

- Root: `sudo` refused (no-new-privileges). `apt-get install` and `apt-get update` into `/var/lib` impossible. Playwright's `--with-deps` impossible.
- Not in the image, installed via Homebrew: `uv`, `node`/`npm`/`npx` (26.13.0 / 11.20.0), `go` 1.27.3, `cargo`/`rustc` 1.99.1, `gnu-time` (`time -v`).
- No `/usr/bin/time`. `gnu-time` from Homebrew stands in: `/home/linuxbrew/.linuxbrew/bin/time`.
- Not in the image: `pip3`, `bc`, `import` (ImageMagick), `xwd`.
- Chromium system libraries: `glib`, `gobject`, `gio`, `atk`, `atk-bridge`, `at-spi2` were missing and installed via Homebrew for the headless shell. Full Chromium still lacks `libcups`, `libcairo`, `libpango`.
- Homebrew's `gnu-time` and libs live under `/home/linuxbrew/.linuxbrew`, which is not on PATH by default. Shells need `PATH` set to use `uv`, `node`, `go`, `cargo`, `gtime`.

## Notes

- Cost line in the session notice was not shown: `AGENT_COST_USD_PER_HOUR` is unset on this runner. Ask the operator if the cost line is meant to be there.
- Env-name check fails on pattern. `GIT_CONFIG_KEY_*` and `CCR_AUTO_MODE_USER_ENV_KEYS_FACT` match `*KEY*` but are not credentials. Consider a stricter name pattern or an exemption list for runner-injected git config.
- Node: TypeScript 7.0.2 build failed without an explicit `types` entry. Confirmed the probe passes with `"types": ["node"]`. I didn't isolate the exact trigger.
- Outputs read speed (1646 MB/s) is likely page cache. A cold read would need a cache drop, which isn't possible unprivileged.
- `/` is an overlay whose upper dir sits under `/tmp/fs`. CLAUDE.md says it's in memory. Not tested directly; the 4 GiB scratch test confirmed scratch is not memory.
- Probe project sources are in `probes/` in this folder. Nothing in them needs a secret.
- Scratch probe dirs under `$AGENT_SCRATCH/runner-test` are not committed.
