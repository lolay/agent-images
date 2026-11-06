# Handoff: m4, functional smoke and versions report

Written 2026-11-06. The design is [specs/gcp.md](../gcp.md) "m4 - Functional smoke
and versions report" (under "Milestones"), with its s1 to s4 and tier tags. This
file is the state around it, not a second copy of the design.

## Where we are

- m1 and m2 are done. m3 (GPU types) is open: `linux-gpu` on an L4 landed in
  lolay/agent-images#37; `3d-gpu` and the RTX PRO 6000 aren't done
  ([handoff-m3-gpu-types.md](handoff-m3-gpu-types.md)). m4 doesn't depend on m3 and
  can run alongside it. The `gpu` toolset's smoke script compiles only, so it
  doesn't wait on a GPU either.
- The spec revision landed on branch `claude/gracious-carson-ebtt7x` (commit
  `1bd3f93`, lolay/agent-images#46): new m4; the old m4 (dependency watch) became m5 and was
  revised to open auto-merging pull requests gated by m4's smoke.
- No m4 code exists yet.

## Decisions (owner, 2026-11-06)

- **Homebrew stays for everything it installs, toolchains included.** In m5's lock its
  versions are a record and a build trigger, never a pin. Don't propose moving Go,
  Node or Python off Homebrew.
- **m4 comes before the watch** because m5 merges lock pull requests on m4's smoke, and
  the versions report is useful now, with or without a lock.
- **Renovate stays out**: it can't resolve the apt closure, the snapshot or Homebrew on
  Linux, so the resolver exists anyway.
- **Per-toolset scripts are written by subagents** under the contract in gcp.md m4
  s3: one file each, offline, 40 lines at most, under 30 s, no git. The parent
  reviews and commits per wave.

## Gotchas

- **Cloud sessions have no Docker daemon.** s1 to s3 can be linted and unit-tested in
  a session (`make cloud-lint cloud-test`), but nothing there runs a script inside an
  image. Image-level checks run in `cloud-build.yml`, which only runs on pull
  requests: open one early, as a draft, and iterate on its runs. A local Mac or Linux
  box with Docker can use `make cloud-build` then `make cloud-smoke TYPE=linux`.
- **Hosted runner disk is about 14 GB free** after the "Free disk" step. Loading
  `gis` (9 GB) or `linux-gpu` (CUDA) for `--load` may not fit. The spec allows those
  two to keep the no-output build and smoke only at `-next`; measure before deciding.
- **The android scripts are first exercised by the weekly android deploy's smoke VM**
  (Sunday 05:37 Pacific), or by a manual `cloud-deploy` dispatch with targets
  `android`, which costs about 45 minutes and $0.50.
- **Builds must stay green with nothing on stderr.** `install-toolset.sh` and the
  `install.sh` scripts send tool progress to stdout (`2>&1`) for that reason; keep
  `write-versions.sh` the same.
- **`brew list --versions` in the image lists everything installed**, dependencies
  included, which is what the report wants. The build-time log line in
  `install-toolset.sh` lists only the Brewfile's own formulae; leave it.
- **The report's layer must be last** (after Claude in `images/cloud/runner/Dockerfile`)
  so a cached copy is still accurate.
- **`tests/unit/test_bake.py` checks the bake file against the types** (from
  lolay/agent-images#44): every `infra/gcp/<type>.env` needs `<type>-tools` and
  `<type>`, and the `FROM` chain must match `changed-targets.sh`. The new
  `versions-<type>` targets have to pass that test, or the test has to learn them.
- **The pull request check builds every type** from `make cloud-targets`
  (lolay/agent-images#44), so s1's `--load` and smoke step goes into that matrix,
  skipping `orchestrator`.
- **`changed-targets.sh` maps paths to types.** A new `smoke.sh` under
  `images/toolsets/<name>/` already rebuilds the types that install that toolset. A
  new `images/toolsets/write-versions.sh` needs adding to the "every runner type" row
  in gcp.md "What a push to `main` deploys" and to the script, the way
  `write-claude-md.sh` is.

## How to start

Read gcp.md m4, then `images/cloud/entrypoint.sh` (`smoke()`, `toolchains`),
`images/toolsets/install-toolset.sh` (`record()`) and `tests/unit/test_entrypoint.py`.
s1 and s2 are `[deep]` and done by the parent. s3 fans out after s1 is committed,
since the scripts need the recording and running in place to be checked. s4 needs a
pull request.
