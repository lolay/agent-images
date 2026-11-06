# `linux` and Anthropic's default cloud environment

How the `linux` runner type (`agent-linux` on Cloud Run, [gcp.md](gcp.md)) compares
with the environment a claude.ai session gets on Anthropic's own cloud: hardware,
cold start with and without our tools, cost, tool versions, network and
credentials. Measured 2026-11-06 from inside an Anthropic-hosted session
(`CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE=cloud_default`, Claude Code 2.1.42) and
Anthropic's [cloud environment docs](https://code.claude.com/docs/en/cloud-environments),
against `images/cloud/linux` as of cbc8465. Both sides track releases, Anthropic's
on its own schedule, so re-measure before relying on a version.

## Summary

Same hardware, opposite philosophies. Anthropic's is a fat, root, general-purpose VM
with many runtimes at fixed, older versions, Docker, browsers, and a proxy in front
of every request that keeps credentials out of the VM. `agent-linux` is a lean,
non-root image with the latest of each toolchain and the Lolay session tools, picked
by the rule in [image-toolsets.md](image-toolsets.md).

Anthropic's environment can carry `agent-linux`'s tools too, and measured, it
starts faster than `agent-linux` does: about 12 s from Anthropic's cache against
about 55 s on Cloud Run. The setup script takes 3 to 4 minutes, paid by the
sessions that start while the cache builds, then again about weekly or after the
script changes. That leaves the real differences as cost, root and Docker on
Anthropic's side, and the outputs and datasets buckets, no egress proxy, larger
machines (`gis`, the GPU types) and full control of the image on ours.

| | Anthropic `cloud_default` | Anthropic + `agent-linux` tools | `agent-linux` |
| --- | --- | --- | --- |
| Cold start | About 12 s, measured by hand | About 12 s from the cache; 2 min 45 s to 3 min 55 s while it builds (below) | About 55 s (m1), plus the orchestrator's queue |
| Compute cost | None beyond the plan | None beyond the plan | About $0.37 an hour per session |
| Tool versions | Fixed by Anthropic, months behind | Latest, as of the cache build | Latest, as of the nightly build |
| Root, apt, Docker | Yes | Yes | No |

## Hardware

| | Anthropic `cloud_default` | `agent-linux` |
| --- | --- | --- |
| Runs on | An isolated Anthropic-managed VM per session; image fixed, extended only by the setup script | A Cloud Run job execution per session, our image |
| OS | Ubuntu 24.04.5, x86_64 | Ubuntu 24.04, x86_64 |
| CPU | 4 vCPU | 4 vCPU (`nproc` says 5) |
| Memory | 16 GB | 16 GiB |
| Disk | 30 GB (the docs' limit; `df` shows the 252 GB volume) | 10 GiB scratch disk at `/scratch`; the rest of the filesystem is memory and counts against the 16 GiB |
| User | `root` | `ubuntu`, no root: Cloud Run sets no-new-privileges, so `sudo` and `apt-get` fail |
| System packages | `apt-get install` works | Not possible; Homebrew, or a Compute Engine type |
| Docker | `docker`, `dockerd`, compose. The daemon isn't running at start; `dockerd &` brings it up (overlayfs, cgroup v1) | None; Cloud Run can't run containers in a session |
| KVM | No `/dev/kvm` | No `/dev/kvm` |
| GPU, display | None | None (the `-gpu` types are m3) |
| Session limits | Reclaimed after inactivity; reopening provisions a fresh VM with the conversation | 60 min idle, 8 h hard limit, both in the session notice |
| Persistence outside git | None | `$AGENT_OUTPUTS` (GCS, 30 days) and read-only `$AGENT_DATASETS` |

## Cold start

What a session waits for between the click and Claude's first command.

| | Anthropic, no setup script | Anthropic + `agent-linux` tools | `agent-linux` |
| --- | --- | --- | --- |
| Measured | About 12 s, by hand, 2026-11-06 | First session 3 min 55 s; second, a few minutes later, 2 min 45 s (the script again, 166 s); third, about 20 minutes later, about 12 s from the cache. All by hand, 2026-11-06 | About 55 s for the 5.5 GB image (Cloud Run streams layers), m1 |
| What's in it | VM from Anthropic's pool, repo clone | First session: the setup script, then Anthropic snapshots the filesystem. Later sessions start from the snapshot and skip the script | The orchestrator's spawn hook, the Cloud Run execution, the runner registering with the work order |
| Repeats when | Every session | The script runs again when the script or the allowed hosts change, or the cache expires (about 7 days) | Every session: no standby (`MIN_IDLE=0`); one would cost about $270 a month |

The cache is the thing to watch. Anthropic caches a setup script only when it
finishes in roughly five minutes; past that, every session runs it. On 2026-11-06 it was, but not at once. The second session, started a
few minutes after the first, ran the script again, though the first had exited 0
in well under the limit: no `setup-script-failed`, 166 s of script, setup ending
40 ms before `CCR_SPAWN_TIMESTAMP_MS`. The third, about 20 minutes later, started
in about 12 s, the same as an environment with no setup script. So the snapshot
seems to build in the background after a successful run, and a session that
starts before it's ready pays for the script again (an inference; the docs don't
say). Per-toolset seconds in the second session: core 55.1, native 6.4, jvm 2.1,
browser 2.0, node 32.4, python 10.2, go 10.0, rust 27.4, proto 10.0, terraform
10.5. If the report shows sessions paying the install every time, run
the toolsets in parallel or drop the ones Anthropic already covers.

`EXPECTED_SPAWN_SECONDS` is 180 for `linux`, three times the measured cold start,
so the orchestrator starts a runner early enough that a queued session rarely
waits on it.

## Cost

| | Anthropic-hosted | `agent-linux` |
| --- | --- | --- |
| Compute | None. "It counts toward your plan's usage limits ... and there's no separate charge for the cloud VM" ([docs](https://code.claude.com/docs/en/claude-code-on-the-web)) | Cloud Run list price for 4 vCPU and 16 GiB, about $0.37 an hour while a session runs ([gcp.md](gcp.md), "Standbys"); a cold start is about half a cent |
| Setup time | Nothing in dollars; the 4¼ minutes cost wall-clock, once per cache build | Image builds run in CI, not per session |
| Idle | Nothing | About nothing: no standby, the orchestrator is on the free-tier `e2-micro`, buckets cost per GB stored ([gcp.md](gcp.md), "Idle cost") |
| Model usage | The plan's usage limits | The same plan's usage limits: self-hosted moves only the compute |
| Limits | Rate limits shared with all the account's Claude use; parallel sessions draw them down faster | The same, plus our own Cloud Run quotas |

So the cost question is only about compute. An 8-hour `linux` session is about
$3 at list price; the same session on Anthropic's VM is nothing extra. What
`agent-linux` buys for that is the buckets, no proxy, a 10 GiB disk we can grow, the
`gis` and GPU types (which Anthropic's 4 vCPU and 16 GB can't serve), and control
over the image.

## Tool versions

Anthropic's column is what this session's VM reports. The `agent-linux` column is
what a build installs today, measured by running agent-linux's toolsets on the same
VM (below); the deployed image is the last nightly's, so it can be a day behind.
Everything from Ubuntu's archive is the same on both, since both are Ubuntu 24.04.

| Tool | Anthropic `cloud_default` | `agent-linux` |
| --- | --- | --- |
| gcc, make | 13.3, 4.3 (Ubuntu) | The same (Ubuntu) |
| clang | 18.1 | No |
| cmake, ninja, pkg-config | 3.28, 1.11, 1.8 (Ubuntu) | The same (Ubuntu) |
| conan | 2.32 | No |
| Native headers | OpenSSL, zlib, libffi, SQLite, bzip2, xz, readline | The same, plus libpq |
| git | 2.43 (Ubuntu) | The same (Ubuntu) |
| gh | 2.89 | 2.103 |
| jq | 1.7 (Ubuntu) | The same (Ubuntu) |
| ripgrep | 14.1 | 15.2 |
| tmux | 3.4 | 3.8 |
| age | 1.1 | 1.3 |
| sops | No | 3.13 |
| shellcheck | No | 0.11 |
| actionlint | No | 1.7.12 |
| mandoc | No | Yes (Ubuntu) |
| triage, nowline | No (triage through Lolay's setup script) | 0.5.0, 0.12.1 |
| yq, vim, nano | Yes | No |
| Node | 22.23 on PATH, 24 in `/opt/node24` (the docs list 20, 21 and 22; this VM has 22 and 24) | 26.13 |
| npm, pnpm, yarn | 10.9, 10.28, 1.22 | npm with node, pnpm 12.16 |
| eslint, prettier | 10.1, 3.8 | No (project dependencies) |
| Bun | 1.4 (the docs warn of proxy issues) | No |
| Python | 3.13.18, and `create-venv-py3.11/12/13` | 3.15.2 |
| uv, ruff | 0.11.32, 0.16.9 | 0.13.7, 0.17.4 |
| poetry, black, mypy, pytest | 2.4, 26.5, 2.3, 9.1 | No (project dependencies) |
| pre-commit | No | 4.7 |
| Python document libraries | pandas, openpyxl, python-docx, python-pptx, pdfplumber, pypdf, reportlab, markitdown | No |
| Go | 1.24.7, golangci-lint | 1.27.3 |
| Rust | 1.97 (rustup 1.28) | 1.99.1 (rustup 1.29), clippy and rustfmt |
| Java | OpenJDK 21.0.12 (Ubuntu) | The same (Ubuntu) |
| Maven, Gradle | 3.9.11, 8.14.3 | No (the project's wrapper) |
| Ruby | 3.1, 3.2, 3.3 (rbenv) | No |
| PHP | 8.3, Composer | No |
| Nix | 2.34 | No |
| protoc, buf, grpcurl | No | 36.2, 1.75, 1.9.4 |
| Terraform, tflint | No | 1.17.1 through tenv 4.15, 0.65 |
| Browser | Chrome for Testing (`$CHROME_PATH`), Playwright 1.63 with Chromium in `/opt/pw-browsers`, chromedriver 147 | Chromium's system libraries; `playwright install chromium` per project |
| ffmpeg | 6.1 (Ubuntu) | No |
| PostgreSQL | 16 server (not on PATH; `/usr/lib/postgresql/16`) and psql | psql client |
| Redis | 7.0 server | No |
| sqlite3 CLI | No | Yes |
| Docker | 29.9 | No |
| gcloud, gsutil, bq | 570 | No |
| Homebrew | No | Yes, owned by the session user |

Read across: Anthropic covers more languages; `agent-linux` covers the Lolay session
tools and is a major version or two ahead on Node, Go and Python. Neither is a
superset of the other.

## Network and proxy

| | Anthropic `cloud_default` | `agent-linux` |
| --- | --- | --- |
| Egress | Every request goes through a local agent proxy (`HTTPS_PROXY=127.0.0.1:…`) to Anthropic's egress proxy, which enforces the environment's allowed hosts and rate limits | Direct, through Cloud NAT; no allowlist |
| TLS | Re-terminated at Anthropic's proxy, so every tool must trust its CA. Anthropic wires it into the system store, the JDK truststore, NSS, Chromium's policies, Bazel, and `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `PIP_CERT`, `CARGO_HTTP_CAINFO`, `JAVA_TOOL_OPTIONS`. A tool with its own trust store fails until pointed at `/root/.ccr/ca-bundle.crt` | End to end; nothing to configure |
| Plain HTTP | Not supported (only `HTTPS_PROXY`) | Works |
| GitHub | A GitHub proxy scoped to the session's repositories. It rejects tag pushes, branch deletions and GraphQL, so `gh pr` and `gh issue` fail; `gh api repos/...` works | The runner's git proxy for the session's repositories |

## Credentials and secrets

Neither image has secrets baked in.

- **Anthropic** keeps them all outside the VM. `GH_TOKEN`, `GITHUB_TOKEN`,
  `CLOUDSDK_AUTH_ACCESS_TOKEN`, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are
  set, but all five hold the same 14-character placeholder (`proxy-injected`, which
  the docs name). The proxy swaps in the real credential after the request leaves
  the VM. The GitHub token comes from the claude.ai GitHub connection. Other APIs
  get **network secrets**: a key stored on the environment and attached by the
  proxy to requests for the hosts you list, never visible in the session. Commits
  are signed by `/tmp/code-sign`, which links to Anthropic's environment manager,
  and the signing key isn't in the VM.
- **`agent-linux`** has none at all, by design: no gcloud, no cloud credentials,
  and deploys run in CI ([secrets.md](secrets.md)). Self-hosted environments get
  no network secrets from claude.ai, so if a `linux` session ever needs an API key,
  that's ours to build.

Environment variables aren't for secrets on either side: on Anthropic's, "every
member's sessions in a shared environment read its variables" ([secrets.md](secrets.md)).

## Claude Code configuration

| | Anthropic `cloud_default` | `agent-linux` |
| --- | --- | --- |
| Claude Code | Preinstalled at `/opt/claude-code`, version set by Anthropic | Native installer at image build (`CLAUDE_VERSION`, default latest), one layer rebuilt per release |
| `~/.claude/CLAUDE.md` | None from the image; a setup script can write one | Generated from the installed toolsets, plus the machine's facts (no sudo, storage, time limit) |
| Hooks from the image | Anthropic's: setup-script-failure notice, git stop check | The shared Stop push reminder and the cloud SessionStart hook (outputs folder, session notice, storage facts) |
| Plugins and skills | The account's claude.ai plugins, synced in at start | Whatever the runner seeds from the image's `~/.claude`; whether claude.ai also syncs account plugins into self-hosted sessions isn't confirmed yet |

## Lolay's customizations on top of `cloud_default`

What this session's environment adds to Anthropic's image, found from what changed
at container start and the setup script's output:

- **Setup script:** downloads triage 0.5.0's Linux release, checks it against the
  release's checksums, and installs it to `/usr/local/bin/triage`, so a repo's
  `make doctor` can run. Nothing else.
- **Synced plugins** (claude.ai account, not the environment): `personal`,
  `engineering` and `workstation`. `personal`'s SessionStart hooks load the personal
  standards and run each repo's `make doctor` (or `triage`).

## Matching agent-linux on Anthropic's cloud

An experiment, not a decision: give an Anthropic environment `agent-linux`'s tools
and compare how fast sessions start and what they get.

`scripts/anthropic/setup.sh` installs `agent-linux`'s toolsets in `agent-linux`'s
order, with the same `install-toolset.sh`, timing each into
`/var/log/agent-images-setup.tsv`. The environment's setup script is one line, and
agent-images must be one of the session's repositories:

```bash
bash agent-images/scripts/anthropic/setup.sh
```

Two things differ from `agent-linux`, both forced by Anthropic's image:

- **Homebrew belongs to `ubuntu`, not root.** Homebrew won't run as root outside a
  container it recognizes (`/.dockerenv`, `/run/.containerenv`, or a docker or
  kubepods cgroup), and Anthropic's VM has none of them. Sessions are root, so a
  `brew` wrapper in `/opt/agent-images/bin` runs it as `ubuntu`.
- **The proxy's CA variables aren't passed to `ubuntu`.** `SSL_CERT_FILE` and the
  rest name `/root/.ccr/ca-bundle.crt`, which `ubuntu` can't read (curl exits 77).
  The system store has the proxy's CA already, so Homebrew works without them.

Measured 2026-11-06 in a `cloud_default` session, running the same toolsets the
same way as the image's `claude` user (which has passwordless sudo already). The
sudoers, wrapper and profile steps weren't run, since the session's permission
mode blocked them. Every toolset installed, nothing failed TLS through the proxy,
and Homebrew ended at 1.7 GB:

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

Most of it is `core`, `node` and `rust`. A script that kept Anthropic's Node, Rust
and Go would take about two minutes, but then the versions aren't `agent-linux`'s.

### The report prompt

Start a session and give it this, with the time you clicked start. Run it four
times: twice on the environment with the setup script (the first session builds
the cache, the second should start from it), once on plain `cloud_default`, and
once on `linux`.

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
   click to your first command. If the setup log's end is before the container's
   spawn, this session started from Anthropic's environment cache: say so. If the
   setup script failed, quote /root/.claude/environment-manager/setup-script-output.

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
   agent-images/specs/anthropic-cloud.md (the Hardware, Cold start and Tool
   versions tables): what's missing, what's older, what's extra.
```

## What breaks moving a repo between them

From Anthropic's to `linux`:

- Anything that runs Docker (Testcontainers, `docker build`, `docker compose`
  services in tests).
- `apt-get install` in a repo's setup or CI-mirroring scripts.
- Ruby, PHP, Bun, Maven or Gradle on PATH without a wrapper: `brew install` them.
- Playwright tests: `playwright install chromium` first (the system libraries are
  already there).
- Anything that expects gcloud, `bq`, or a network secret.
- Files written outside `/scratch` beyond a few GB: they count against memory.

From `linux` to Anthropic's:

- sops, shellcheck, actionlint, nowline, mandoc, Terraform/tflint, protoc/buf/grpcurl
  and the sqlite3 CLI aren't there, without the setup script above.
- Toolchain versions are older and fixed (Go 1.24, Node 22, Python 3.13); a repo
  with a `go.mod` toolchain line or an engines field ahead of them downloads or
  fails.
- Every outbound tool has to trust the proxy's CA, and only allowed hosts answer.
- `gh pr` and `gh issue` fail on the GitHub proxy's GraphQL block; use
  `gh api repos/...`.
- No `$AGENT_OUTPUTS` or `$AGENT_DATASETS`: large outputs have nowhere to go but git.
- No KVM on either, so the Android emulator needs the `android` type.

## Re-measuring

From a session on Anthropic's cloud: `check-tools` prints Anthropic's own version
list, `cat /root/.claude/environment-manager/setup-script-output` shows what the
setup script did, and `find /usr /opt /etc -xdev -newermt <today>` lists what changed
at start. For `agent-linux`, the image's `/etc/agent-images/toolsets/` records what
each toolset installed, and the build log's `install-toolset` lines carry the
versions.
