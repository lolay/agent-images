# Test: `linux` runner

You're in a session on the `linux` runner type: a CPU-only Linux container that runs
one session and is thrown away. Exercise it the way everyday development would and
write a report. Follow the steps in order; if a check fails, record it and carry on.
Time every step. Work in a scratch directory outside the repo
(`$AGENT_SCRATCH/runner-test` (`$HOME/runner-test` if that isn't set)), and write outputs to
`tests/sessions/results/linux/<UTC timestamp>/` in the repo.

## 1. Facts

Record: `uname -a`, `/etc/os-release`, `nproc`, `free -h`, `df -h` for `/` and the
repo, the user, whether `sudo -n true` works, `claude --version`, and the published
latest from `https://downloads.claude.ai/claude-code-releases/latest` (pass if they
match). Record whether `$DISPLAY` is set.

List environment variable names only, never values, and fail the check if any name
looks like a credential (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`), except
`SELF_HOSTED_RUNNER_*` and Claude's own session variables; note those separately.

Check the cost notice: the session should have opened with a line like "⚠ This
runtime costs approximately $X/hour". Record the line as shown, `AGENT_COST_USD_PER_HOUR`
and `AGENT_COST_DETAIL`. Pass if the line appeared and its figure matches the
variable. Note whether it came from the hook's message or from Claude's first reply.

## 2. Storage

Record `$AGENT_SCRATCH`, `$AGENT_OUTPUTS` and `$AGENT_DATASETS`, what's mounted at
each (`findmnt`), and the free space on each. Pass if `$AGENT_OUTPUTS` ends in this
session's id, and if what you were told about storage at the start of the session
(the image's `CLAUDE.md` and the start-of-session facts) matches what you find.
Then:

- Write a 4 GiB file to scratch with `dd` (`oflag=direct`), read it back, and record
  MB/s both ways. Check that memory use (`free -m`) didn't grow by 4 GiB: that would
  mean scratch is really the in-memory filesystem.
- Copy a 1 GiB file to `$AGENT_OUTPUTS`, read it back, compare checksums, and record
  MB/s. Then delete it. Pass if the checksums match.
- List `$AGENT_DATASETS`, and check a write there fails: it's read-only to sessions.

Pass the step if scratch is a real disk of at least the type's size, outputs
round-trips, and datasets is read-only.

## 3. Isolation

Check for `$HOME/.runner-test-marker`. Pass if it doesn't exist: a fresh runner never
has one. Then create it with the timestamp and the session's hostname, so a second
run of this test (in a new session) proves the first left nothing behind.

## 4. Network

Time a request to each and record status and latency: `https://pypi.org/simple/`,
`https://registry.npmjs.org/`, `https://index.crates.io/config.json`,
`https://proxy.golang.org/`, `https://ghcr.io/v2/`, `https://formulae.brew.sh/api/formula.json`
(headers only), and the Ubuntu archive (`apt-get update`). Pass if all answer.

## 5. Toolchains

Install whatever isn't there (Homebrew or apt, the way a session would) and record
what you had to install and how long it took:

- Python: create a project with `uv`, one function and a `pytest` test, run it.
- Node: a TypeScript module with one test using the built-in `node --test`, compiled
  with `tsc`.
- Go: a module with one function and a table test, `go test ./...`.
- Rust: `cargo new`, one function and a unit test, `cargo test`.

Pass each if its tests pass.

## 6. A real build

Clone `https://github.com/BurntSushi/ripgrep` (shallow) and run
`cargo build --release` then `cargo test --release`. Record wall time and peak memory
(`/usr/bin/time -v`). This is the CPU baseline that later runs compare against.

## 7. Browser

Install Playwright's Chromium (`npx playwright install --with-deps chromium`), then
screenshot `https://example.com` headless to `browser.png`. Pass if the PNG exists and
isn't blank (more than one distinct pixel color). If `$DISPLAY` is set, also launch
Chromium headed on it and screenshot the display (`import -window root` or `xwd`) to
`display.png`.

## 8. Git

Commit the results directory on the session's branch and push. Pass if the push
succeeds. Don't open a pull request.

## 9. Report

Write `report.md` in the results directory in the format from
[README.md](README.md), commit and push it, and end the session with the check table
and the three slowest steps.
