# Test: `linux` runner

You're in a session on the `linux` runner type: a CPU-only Linux container that runs
one session and is thrown away. Exercise it the way everyday development would and
write a report. Follow the steps in order; if a check fails, record it and carry on.
Time every step. Work in a scratch directory outside the repo
(`$HOME/runner-test`), and write outputs to
`tests/sessions/results/linux/<UTC timestamp>/` in the repo.

## 1. Facts

Record: `uname -a`, `/etc/os-release`, `nproc`, `free -h`, `df -h` for `/` and the
repo, the user, whether `sudo -n true` works, `claude --version`, and the published
latest from `https://downloads.claude.ai/claude-code-releases/latest` (pass if they
match). Record whether `$DISPLAY` is set.

List environment variable names only, never values, and fail the check if any name
looks like a credential (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`), except
`SELF_HOSTED_RUNNER_*` and Claude's own session variables; note those separately.

## 2. Isolation

Check for `$HOME/.runner-test-marker`. Pass if it doesn't exist: a fresh runner never
has one. Then create it with the timestamp and the session's hostname, so a second
run of this test (in a new session) proves the first left nothing behind.

## 3. Network

Time a request to each and record status and latency: `https://pypi.org/simple/`,
`https://registry.npmjs.org/`, `https://index.crates.io/config.json`,
`https://proxy.golang.org/`, `https://ghcr.io/v2/`, `https://formulae.brew.sh/api/formula.json`
(headers only), and the Ubuntu archive (`apt-get update`). Pass if all answer.

## 4. Toolchains

Install whatever isn't there (Homebrew or apt, the way a session would) and record
what you had to install and how long it took:

- Python: create a project with `uv`, one function and a `pytest` test, run it.
- Node: a TypeScript module with one test using the built-in `node --test`, compiled
  with `tsc`.
- Go: a module with one function and a table test, `go test ./...`.
- Rust: `cargo new`, one function and a unit test, `cargo test`.

Pass each if its tests pass.

## 5. A real build

Clone `https://github.com/BurntSushi/ripgrep` (shallow) and run
`cargo build --release` then `cargo test --release`. Record wall time and peak memory
(`/usr/bin/time -v`). This is the CPU baseline that later runs compare against.

## 6. Browser

Install Playwright's Chromium (`npx playwright install --with-deps chromium`), then
screenshot `https://example.com` headless to `browser.png`. Pass if the PNG exists and
isn't blank (more than one distinct pixel color). If `$DISPLAY` is set, also launch
Chromium headed on it and screenshot the display (`import -window root` or `xwd`) to
`display.png`.

## 7. Git

Commit the results directory on the session's branch and push. Pass if the push
succeeds. Don't open a pull request.

## 8. Report

Write `report.md` in the results directory in the format from
[README.md](README.md), commit and push it, and end the session with the check table
and the three slowest steps.
