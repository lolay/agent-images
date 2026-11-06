# `linux` and Anthropic's default cloud environment

How the `linux` runner type (`agent-linux` on Cloud Run, [gcp.md](gcp.md)) compares
with the environment a claude.ai session gets when it picks Anthropic's own cloud
instead of one of ours. Measured 2026-11-06 from inside an Anthropic-hosted session
(`CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE=cloud_default`, Claude Code 2.1.42), against
`images/cloud/linux` as of cbc8465. Anthropic changes its image without notice, so
treat the versions as a snapshot and re-measure before relying on one.

## Summary

They solve different problems. Anthropic's environment is a fat, root, general-purpose
box: many language runtimes at fixed versions, Docker, browsers, gcloud, and a
proxy in front of every outbound request. `agent-linux` is a lean, non-root,
latest-everything image chosen by the rule in
[image-toolsets.md](image-toolsets.md): what a session can't install itself, plus the
toolchains used most days, with Homebrew so a session adds the rest. A repo that
works on `agent-linux` will almost always work on Anthropic's; the reverse fails
for Docker, sudo/apt, and the tools only Anthropic ships (below).

Pick Anthropic's environment for anything that needs Docker or root, or a runtime
`agent-linux` leaves out (Ruby, PHP, Bun, Maven). Pick `linux` for current
toolchains, the Lolay session tools out of the box (sops, shellcheck, actionlint,
nowline, Terraform, protobuf), a 10 GiB scratch disk plus the outputs and datasets
buckets, and no egress proxy.

## The machine

| | Anthropic `cloud_default` | `agent-linux` |
| --- | --- | --- |
| Runs on | Anthropic-hosted VM (Firecracker); base image fixed, extended only by a setup script | Cloud Run job execution, one per session; our image |
| OS | Ubuntu 24.04.5, x86_64 | Ubuntu 24.04, x86_64 |
| CPU and memory | 4 vCPU, 16 GB | 4 vCPU (`nproc` says 5), 16 GiB |
| Disk | 252 GB volume, about 30 GB writable per session (an allowance, so `df` misleads) | 10 GiB scratch disk at `/scratch`; everything else is memory and counts against the 16 GiB |
| User | `root` (with a `claude` user present, unused) | `ubuntu`, no root: Cloud Run sets no-new-privileges, so `sudo` and `apt-get` fail |
| Installing system packages | `apt-get install` works | Not possible; Homebrew (owned by `ubuntu`) or a Compute Engine type |
| Docker | CLI and `dockerd` installed; the daemon isn't running at start, but `dockerd &` as root brings it up (overlayfs, cgroup v1) | None; Cloud Run can't run containers in a session |
| KVM | No `/dev/kvm` | No `/dev/kvm` |
| Session length | Container reclaimed after inactivity | 60 min idle, 8 h hard limit, shown in the session notice |
| Persistence outside git | None | `$AGENT_OUTPUTS` (GCS, 30 days) and read-only `$AGENT_DATASETS` |

## Network and credentials

| | Anthropic `cloud_default` | `agent-linux` |
| --- | --- | --- |
| Egress | Through a local agent proxy (`HTTPS_PROXY=127.0.0.1:…`) under the environment's network policy, with its own CA injected into the system store, the JDK truststore, NSS, Chromium policies, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `PIP_CERT`, `CARGO_HTTP_CAINFO`, Bazel and `JAVA_TOOL_OPTIONS` | Direct, through Cloud NAT. No proxy, no extra CA, nothing to configure per tool |
| GitHub | A git proxy scoped to the session's repositories; `gh` CLI present but the harness steers the agent to the GitHub MCP tools | The runner's git proxy for the session's repositories (same claude.ai mechanism) |
| Commit identity | `Claude <noreply@anthropic.com>`, SSH-signed through `/tmp/code-sign` | Set by the runner |
| Cloud credentials | gcloud, gsutil and bq installed and preconfigured for the proxy | None, by design (no gcloud, no credentials; deploys run in CI) |
| Secrets | Environment variables and setup script in the environment's settings (not meant for secrets, per [secrets.md](secrets.md)) | None for sessions; same policy |

## Toolchains

Versions are what each reports today. `agent-linux` installs the newest release at
every build ([design.md](design.md), "Track the latest"), so its column moves with
each nightly build; Anthropic's moves when Anthropic rebuilds.

| Tool | Anthropic `cloud_default` | `agent-linux` |
| --- | --- | --- |
| C/C++ | gcc 13.3, clang 18.1, make, cmake 3.28, ninja, pkg-config, conan | build-essential (gcc 13), cmake, ninja, pkg-config (`native` toolset) |
| Native headers | libssl, zlib, libffi, libsqlite3, bz2, lzma, readline | The same, plus libpq-dev |
| Node | 22.23 (and 24 via `use-node-24`, nvm), npm, pnpm 10, yarn 1, eslint, prettier | Homebrew's latest node, pnpm |
| Bun | 1.4 | No |
| Python | 3.13 at `/usr/local/bin` (`use-python`, `create-venv-py3.11/12/13`), pip, uv, ruff, poetry, black, mypy, pytest, plus document libraries (pandas, openpyxl, python-docx, pdfplumber, markitdown) | Homebrew's latest python, uv, ruff, pre-commit |
| Go | 1.24.7, golangci-lint | Homebrew's latest go |
| Rust | rustup with 1.97 stable in `/root/.cargo` | rustup (Homebrew) with stable, clippy and rustfmt; `CARGO_HOME` on scratch |
| JVM | OpenJDK 21, Maven 3.9, Gradle 8.14 | OpenJDK 21 (`jvm` toolset); Gradle from the project's wrapper |
| Ruby | 3.1, 3.2, 3.3 through rbenv | No (`brew install ruby`) |
| PHP | 8.3, composer | No |
| Nix | 2.34 | No |
| Protobuf | No | protoc, buf, grpcurl |
| Terraform | No | tenv with the latest Terraform, tflint |
| Browsers | Playwright 1.63 with Chromium, headless shell and ffmpeg in `/opt/pw-browsers`; chromedriver | Chromium's system libraries only; `playwright install chromium` per project |
| ffmpeg | Yes (Ubuntu's 6.1) | No |
| Databases | psql client, redis-server | psql and sqlite3 clients |

## Session tools

The `core` toolset is the list Lolay sessions expect on every platform. Anthropic's
environment has about half of it.

| Tool | Anthropic `cloud_default` | `agent-linux` |
| --- | --- | --- |
| git | 2.43 (Ubuntu) | Ubuntu's |
| gh | 2.89 | Homebrew's latest |
| jq, ripgrep, tmux | Yes (Ubuntu's) | jq from Ubuntu, the rest from Homebrew |
| age | 1.1 (Ubuntu) | Homebrew's latest |
| sops | No | Yes |
| shellcheck, actionlint | No | Yes |
| mandoc | No | Yes |
| triage | Only through Lolay's setup script (below) | Yes, from `lolay/tap` |
| nowline | No | Yes, from `lolay/tap` |
| Homebrew | No | Yes, owned by the session user |
| sqlite3 CLI | No | Yes |

## Claude Code configuration

| | Anthropic `cloud_default` | `agent-linux` |
| --- | --- | --- |
| Claude Code | Preinstalled at `/opt/claude-code`, version set by Anthropic | Native installer at image build (`CLAUDE_VERSION`, default latest); one layer, rebuilt per release |
| `~/.claude/CLAUDE.md` | None from the image | Generated from the installed toolsets, plus the machine's facts (no sudo, storage, time limit) |
| Hooks from the image | Anthropic's: setup-script-failure notice, git stop check | The shared Stop push reminder and the cloud SessionStart hook (outputs folder, session notice, storage facts) |
| Plugins and skills | The account's claude.ai plugins are synced in at start | Whatever the runner seeds from the image's `~/.claude`; whether claude.ai also syncs account plugins into self-hosted sessions isn't confirmed yet |

## Lolay's customizations on top of `cloud_default`

What this session's environment adds to Anthropic's image, found from what changed
at container start and the setup script's output:

- **Setup script:** downloads triage 0.5.0's Linux release, checks it against the
  release's checksums, and installs it to `/usr/local/bin/triage`. That's the only
  thing it adds to the image, so a repo's `make doctor` can run.
- **Synced plugins** (claude.ai account, not the environment): `personal`, `engineering`
  and `workstation`. `personal`'s SessionStart hooks load the personal standards and
  run each repo's `make doctor` (or `triage`) and report it.

Everything else in the tables above is Anthropic's default image. So a Lolay session
on Anthropic's cloud still lacks sops, shellcheck, actionlint, nowline, mandoc,
Terraform and protobuf unless the setup script grows to install them. The cheapest
way to close that gap is Homebrew's `core`, `proto` and `terraform` Brewfiles from
this repo in the setup script, at the cost of a slower session start (Homebrew's
install alone is a minute or more).

## Matching agent-linux on Anthropic's cloud

An experiment, not a decision: give an Anthropic environment agent-linux's tools
and compare how fast sessions start and what they get. If the start cost is
tolerable, it's a way to run Lolay sessions with the same tools without our GCP
backend, and with Docker and root besides.

`scripts/anthropic/setup.sh` installs agent-linux's toolsets, in agent-linux's order,
with the same `install-toolset.sh`, timing each into
`/var/log/agent-images-setup.tsv`. The environment's setup script is one line, and
agent-images must be one of the session's repositories:

```bash
bash agent-images/scripts/anthropic/setup.sh
```

Two things differ from agent-linux, both forced by Anthropic's image:

- **Homebrew belongs to `ubuntu`, not root.** Homebrew won't run as root outside a
  container it recognizes (`/.dockerenv`, `/run/.containerenv`, or a docker or
  kubepods cgroup), and Anthropic's VM has none of them. Sessions are root, so a
  `brew` wrapper in `/opt/agent-images/bin` runs it as `ubuntu`.
- **The proxy's CA variables aren't passed to `ubuntu`.** `SSL_CERT_FILE` and the
  rest name `/root/.ccr/ca-bundle.crt`, which `ubuntu` can't read (curl exits 77).
  The system store has the proxy's CA already, so Homebrew works without them.

Measured 2026-11-06 in a `cloud_default` session, running the same toolsets the
same way as the image's `claude` user (which has passwordless sudo already); the
sudoers, wrapper and profile steps weren't run, since the session's permission mode
blocked them. Every toolset installed, nothing failed TLS through the proxy, and
Homebrew ended at 1.7 GB:

| Toolset | Seconds | What took the time |
| --- | --- | --- |
| core | 79.5 | Homebrew's own install, then its formulae |
| native | 5.1 | Nearly all already on Anthropic's image |
| jvm | 3.0 | Already there |
| browser | 3.2 | Already there |
| node | 51.8 | Homebrew's node |
| python | 15.2 | |
| go | 14.8 | |
| rust | 49.8 | rustup's stable toolchain |
| proto | 16.6 | |
| terraform | 15.8 | |
| **Total** | **254.9** | |

What it got, all newer than Anthropic's own: Go 1.27.3 (against 1.24.7), Node 26.13
(22.23), Python 3.15.2 (3.13.18), Cargo 1.99.1 (1.97), plus sops 3.13.3, actionlint
1.7.12, shellcheck, nowline 0.12.1, protoc 36.2, buf 1.75, Terraform 1.17.1 and
tflint 0.65.

So the setup script adds about four and a quarter minutes to every session start,
on top of Anthropic's own spawn, since the setup script runs per session. agent-linux's
cold start on Cloud Run is about 55 s, the toolsets being in the image. Most of the
four minutes is `core`, `node` and `rust`; a script that skipped what Anthropic's
image already has (keeping Anthropic's Node, Rust and Go) would cut it to about
two, but then the versions aren't agent-linux's. The prompt below measures the
whole start, setup script included, from the click.

### The report prompt

Start a session in the environment and give it this. Run it once on an environment
with the setup script, once on plain `cloud_default` for a baseline, and once on
`linux` for agent-linux's side. Note the time you click start and put it in the
prompt.

```text
I started this session at <HH:MM:SS, your time zone>. Don't install, fix or
change anything: this is a measurement. Write report.md at the root of the
agent-images checkout, commit it and push it to this session's branch. Report
facts only; where something can't be measured from inside the session, say so
rather than estimate.

1. Environment. Which one this is: Anthropic cloud_default with the agent-images
   setup script, without it, or agent-images' own linux type (Cloud Run; look for
   /etc/agent-images/toolsets and $AGENT_TYPE). User, nproc, memory, free disk,
   Claude Code version, whether sudo and apt-get work, whether /dev/kvm exists,
   whether `dockerd` can start (start it in the background, check `docker info`,
   stop it).

2. Start time. Every timestamp you can find, converted to the same time zone as
   my click time, in order: my click time; the container's spawn
   (CCR_SPAWN_TIMESTAMP_MS if set, else the start time of PID 1 from
   `ps -o lstart= -p 1`); the setup script's start and end and each toolset's
   seconds (/var/log/agent-images-setup.tsv, if present); the Claude Code process
   start (`ps -o lstart= -p $CLAUDE_PID`, or the oldest `claude` process); and now,
   at your first command. Give the gap between each pair, and the total from my
   click to your first command. If the setup script failed, quote
   /root/.claude/environment-manager/setup-script-output.

3. Tools. For every toolset in agent-images/images/toolsets that has a check.txt,
   run each check (format `label: command`, must exit 0, first line of output is
   the version) and report pass or fail and the version. Then for go, node,
   python3, cargo, rustc, java, gh, git, jq, rg, terraform and protoc, give
   `command -v` and `type -a` so it's clear whose copy wins (Homebrew's under
   /home/linuxbrew, Anthropic's under /usr/local, /opt or /root, or Ubuntu's in
   /usr/bin). Say whether PATH has /home/linuxbrew/.linuxbrew/bin ahead of
   /usr/local/bin in your Bash tool's shell, and in `bash -lc 'echo $PATH'`.

4. Homebrew from the session. `brew --version`, then time `brew install hello`
   and run `hello`. Report who owns /home/linuxbrew and its size (`du -sh`).

5. Doctor. Quote verbatim any SessionStart hook output headed "Environment
   doctor" that you received, or say there was none. Then run `make doctor` in
   agent-images and give its summary line.

6. A table comparing this run's results with agent-linux's, from
   agent-images/specs/anthropic-cloud.md (the Machine, Toolchains and Session
   tools tables) and gcp.md's "m1 results" (cold start about 55 s): what's
   missing, what's older, what's extra.
```

## What breaks moving a repo between them

From Anthropic's to `linux`:

- Anything that runs Docker (Testcontainers, `docker build`, `docker compose`
  services in tests).
- `apt-get install` in a repo's setup or CI-mirroring scripts.
- Ruby, PHP, Bun, Maven or Gradle on PATH without a wrapper: `brew install` them.
- Playwright tests: `playwright install chromium` first (the system libraries are
  already there).
- Anything that expects gcloud or `bq`.
- Files written outside `/scratch` beyond a few GB: they count against memory.

From `linux` to Anthropic's:

- sops, shellcheck, actionlint, nowline, mandoc, Terraform/tflint, protoc/buf/grpcurl
  and the sqlite3 CLI aren't there; `apt-get` or the setup script covers most.
- Toolchain versions are older and fixed (Go 1.24, Node 22, Python 3.13), where
  `linux` has the latest; a repo with a `go.mod` toolchain line or an engines field
  ahead of them downloads or fails.
- Every outbound tool has to trust the proxy's CA. Anthropic configures the common
  ones; a tool with its own trust store (a JVM it didn't install, a static binary
  with bundled roots) fails TLS until pointed at `/root/.ccr/ca-bundle.crt`.
- No `$AGENT_OUTPUTS` or `$AGENT_DATASETS`: large outputs have nowhere to go but git.
- No KVM on either, so the Android emulator needs the `android` type.

## Re-measuring

From a session on Anthropic's cloud: `check-tools` prints Anthropic's own version
list, `cat /root/.claude/environment-manager/setup-script-output` shows what the
setup script did, and `find /usr /opt /etc -xdev -newermt <today>` lists what changed
at start. For `agent-linux`, the image's `/etc/agent-images/toolsets/` records what
each toolset installed, and the build log's `install-toolset` lines carry the
versions.
