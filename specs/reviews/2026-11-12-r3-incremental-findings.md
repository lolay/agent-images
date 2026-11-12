# r3 incremental review: findings

- Kind: incremental
- Range: 28ca834be3ded803eb35eff51be0bdc3f0d99b06..c20c32aed583b05e007f994a05006e589189bfb4
- Repo: lolay/agent-images
- Follows: r2 (incremental, 506a1b9..28ca834); covers m5 (lolay/agent-images#54, #56, #71), r2's remediation (#52, #53), the lock bot's first lock pull requests (#58 to #78) and #55, #57, #61, #65, #67, #68, #70, #74, #76, #77, #79, #80
- Reviewers: correctness (/code-review xhigh), drift, tests, duplication, fix review; Opus, high (correctness xhigh), fresh context
- Verifier: dropped 1 of 24, merged 1 (Opus, xhigh, fresh context)
- Accepted, not raised again: r2.f16

## correctness

### r3.f1 - The lock watch's carry reverts a lock that changed on `main` while a lock pull request was open

- Severity: medium
- Dimension: correctness
- Kind: fix
- Location: `scripts/cloud/lock-watch.sh:100-115` at c20c32a (with `.github/workflows/lock-watch.yml:97-117`, `lock-watch.sh:117-124,317-336`)
- Evidence: for each type the run didn't resolve, `cmd_carry` copies the open pull request's whole lock (`git show "$from:$path"`) into the candidate, without checking whether `main` changed that lock since the branch left it. An hourly run (class nightly) carries the weekly types' candidates; `proposed` then fails because `main` moved, so `propose` commits the carried lock onto `main`'s head with auto-merge on. A lock changed on `main` in the meantime, by a constraint change re-locked with `make cloud-lock WRITE=1` or by gcp.md's rollback ("a pull request reverting the lock on `main`"), is reverted by the next hourly run. The same root keeps a red carried weekly candidate in every hourly proposal until the weekly run.
- Verified: simulated in a throwaway repo. `lock/watch` = M0 plus gis at claude 9.9.1; `main` moves to M1 with gis at claude 2.0.0. `carry nightly` prints "carried images/cloud/gis/versions.lock", `changed` lists gis, `proposed` fails, and the push's title is "lock(gis): claude 9.9.1" with body `claude/-: 2.0.0 -> 9.9.1`. `test_carry_ignores_what_main_changed_since` covers only a lock the branch didn't touch; nothing in cloud-build checks a lock against its constraints.
- Recommendation: carry a lock only when `main`'s copy is unchanged since the merge-base (`git diff --quiet "$(git merge-base "$main" "$from")" "$main" -- "$path"`), else drop it and let that type's next resolve propose afresh; test it with the sequence above.
- Status: open

### r3.f2 - r2.f9's supervisor refusal stops every type's orchestrator after an env-file-only change

- Severity: medium
- Dimension: correctness
- Kind: fix
- Location: `images/cloud/orchestrator/supervisor.sh:101-108` at c20c32a (with `scripts/cloud/deploy.sh:158`, `scripts/cloud/changed-targets.sh:77-90`)
- Evidence: #53 made the supervisor exit 1 at start when a compute type has `DEPLOY=false` (before, it skipped that type), relying on "the deploy refuses it first, so a bad env file never reaches the host". A commit that only adds `DEPLOY=false` to `infra/gcp/android.env` plans an orchestrator-only deploy (r1.f20: a compute type's env file isn't a VM input), so `load_compute_type`'s refusal never runs. The orchestrator image copies `infra/gcp/`, the host restarts the container, and the supervisor exits, taking down every type's orchestrator in a restart loop. The rule is also checked in two places with two messages (deploy.sh:158, supervisor.sh:105-107).
- Verified: `changed-targets.sh` on such a commit gives `{"types":[],"vms":[],"orchestrator":true}`; `deploy.sh orchestrator` only sets metadata (r1.f10, no smoke); nothing at PR time reads the real env files for this rule. Latent: it needs a key gcp.md forbids, but the blast radius is every type.
- Recommendation: one check in hooks/gcp/lib.sh (`gcp_check_type`), run over the real `infra/gcp/*.env` by `make cloud-test` so the pull request fails; the supervisor logs and skips the bad type instead of exiting.
- Status: open

### r3.f3 - bake.sh builds with a partial set of lock arguments

- Severity: low
- Dimension: correctness
- Kind: fix
- Location: `scripts/cloud/bake.sh:32-37` at c20c32a (with `scripts/cloud/lock.sh:361-380`)
- Evidence: bake.sh treats only an empty list as `lock.sh bake-args` failing ("an empty list means it did"), but bake-args dies mid-loop on an unknown kind after printing the lines before it.
- Verified: with `go - 1.27.0` added after linux's claude line, bake-args printed base, apt-snapshot and claude and died; bake.sh (fake docker) ran bake with those three `--set`s and exited 0, so rust, terraform and tflint fell back to install.sh's defaults. The deploy's `lock="$(lock.sh bake-args …)"` fails closed, so it's a green PR and then a red deploy.
- Recommendation: read bake-args into a variable under `set -e` (`args="$(lock.sh bake-args "$target")"`), then split it, so any failure stops the build.
- Status: open

### r3.f4 - A change to lock.sh's bake-args reaches no image until the scheduled rebuild

- Severity: low
- Dimension: correctness
- Kind: fix
- Location: `scripts/cloud/changed-targets.sh:68-72` at c20c32a (with `scripts/cloud/lock.sh:361-380`, `.github/workflows/cloud-deploy.yml:172`, `specs/gcp.md:275,283`)
- Evidence: the deploy takes every lock-derived build argument from `lock.sh bake-args`, including the `release <name>` to `<NAME>_VERSION` rule, but neither `every_type` nor gcp.md's map names `scripts/cloud/lock.sh`, so it falls under "Anything else: nothing".
- Verified: `changed-targets.sh c12bf71 c20c32a` (#80 edits lock.sh) plans no types. The PR check builds every type on `scripts/cloud/` changes, so a bake-args fix passes it and then waits for the nightly (linux) or weekly (gis, linux-gpu) rebuild.
- Recommendation: add `scripts/cloud/lock.sh` to `every_type` and gcp.md:275's row, or move bake-args into its own file and list that.
- Status: open

### r3.f5 - The lock watch's Close step reports success when the close fails

- Severity: low
- Dimension: correctness
- Kind: fix
- Location: `.github/workflows/lock-watch.yml:193-197` at c20c32a
- Evidence: `scripts/cloud/lock-watch.sh close | tee -a "$GITHUB_STEP_SUMMARY"` with no `shell:` runs under Actions' default `bash -e {0}`, without pipefail, so the step takes tee's status. Every other piped step in the file sets `set -euo pipefail`.
- Verified: confirmed by reading; the next run retries the close, so the cost is a green run with a stale pull request.
- Recommendation: add `set -euo pipefail` to the step, as the others have.
- Status: open

### r3.f6 - `lock.sh diff` misjoins under a non-C locale

- Severity: low
- Dimension: correctness
- Kind: fix
- Location: `scripts/cloud/lock.sh:385-390` at c20c32a
- Evidence: `cmd_diff` sorts both sides with `LC_ALL=C` but runs `join` in the caller's locale, whose collation orders keys like `apt-floating/`, `apt-snapshot/` and `apt/` differently.
- Verified: with en_US.UTF-8 built by `localedef`, linux's real lock against a copy with one package removed and the snapshot moved gives "join: input is not in sorted order", 16 spurious added and removed lines, exit 1, and `summary` says "apt 15 packages"; C and C.UTF-8 give the right 2 lines. Hosted runners use C.UTF-8, so this hits `make cloud-lock` on a workstation, not the watch.
- Recommendation: run `join` (or the whole function) with `LC_ALL=C`, and test `diff` under a non-C collation.
- Status: open

### r3.f7 - The watch can re-propose a lock pull request that merged while it was resolving

- Severity: low
- Dimension: correctness
- Kind: fix
- Location: `.github/workflows/lock-watch.yml:97-123` at c20c32a (with `scripts/cloud/lock-watch.sh:317-336`)
- Evidence: Decide reads the open pull requests after the resolve, compares the candidate with the checkout (`github.sha`), and propose commits onto `main`'s head read later still. If the lock pull request auto-merges during the resolve, Decide sees none open and a candidate that differs from the stale checkout, and pushes the same locks onto the new `main`.
- Verified: by trace. The result is a new pull request with an empty diff, which cloud-build's `changes` passes and auto-merge lands (or, if createCommitOnBranch refuses an empty commit, a failed run; untested, GraphQL isn't reachable here). Noise only; no wrong lock lands.
- Recommendation: in Decide, compare the candidate with `origin/main` fetched after the resolve, not the checkout, and have propose skip when the commit's tree equals `main`'s.
- Status: open

### r3.f8 - `~> x.y` means the x.y line to the lock and `>= x.y, < (x+1).0` to tenv

- Severity: low
- Dimension: correctness
- Kind: decision
- Location: `scripts/cloud/lock.sh:186-209` at c20c32a (with `images/toolsets/terraform/install.sh:6-8`, `specs/gcp.md:210-216,612`, `scripts/anthropic/setup.sh`)
- Evidence: `resolve_terraform` reads `~> 1.14` as the newest 1.14.x, as gcp.md's lock table says; HashiCorp's and tenv's `~> 1.14` is >= 1.14, < 2.0, and gcp.md's pin table says "tenv takes the constraint as is". `terraform_newest`'s `grep -F "1.14."` is also unanchored.
- Verified: narrowed. No VM image installs terraform, so the consumer of the raw constraint is `scripts/anthropic/setup.sh`: once someone pins `~> x.y`, an Anthropic environment and agent-linux would differ. Latent: the default is `latest`. The unanchored grep matters only from Terraform 11.
- Recommendation: pick one meaning. Recommended: tenv's (HashiCorp's), so the constraint means what Terraform users expect, with `~> 1.14.0` for a line; fix gcp.md's lock table to match and anchor the grep. Or keep the line meaning and say in the pin table that `~> x.y` holds the x.y line.
- Status: open

### r3.f9 - Nightly compute types would get `CLAUDE_UPGRADE=false` with no watch locking their Claude

- Severity: info
- Dimension: correctness
- Kind: fix
- Location: `hooks/gcp/lib.sh:61-71` at c20c32a (with `scripts/cloud/lock-watch.sh:71-80`)
- Evidence: `gcp_runner_settings` keys on `REBUILD` alone; the watch resolves only `BACKEND=run` types. A compute type with `REBUILD=nightly`, or none (the default), would run the Claude its last Packer build got, up to a day old, not the hourly watch's.
- Verified: latent; android is weekly and the only compute type.
- Recommendation: emit `CLAUDE_UPGRADE=false` only for nightly Cloud Run types (`gcp_backend` == run).
- Status: open

## drift

### r3.f10 - Every `linux` lock bump rebuilds and pushes `linux-gpu`

- Severity: medium
- Dimension: drift
- Kind: fix
- Location: `scripts/cloud/lib.sh:50-57` at c20c32a (with `scripts/cloud/changed-targets.sh:115-116`, `specs/gcp.md:277,513-520`)
- Evidence: `cloud_image_paths` emits `images/cloud/<dir>/*` for the type and each build parent, and m5 put each lock in its type's directory, so linux's `versions.lock` counts as a linux-gpu input. linux-gpu builds from its own lock (bake-args) and no Dockerfile copies a lock. gcp.md:277 says a type's `versions.lock` redeploys "That type"; gcp.md:513-520 makes linux-gpu weekly and uncached (12 to 15 min).
- Verified: `changed-targets.sh 0af2d6e^ 0af2d6e` (linux lock only) plans linux and linux-gpu; deploy run 42432105264 (#58) built linux-gpu from 01:08 to 01:19. Ten linux-lock-only commits in range. test_changed_targets has no lock-only case. Today it costs runner time and a push (linux-gpu is `DEPLOY=false`); once it deploys, each linux lock merge buys a GPU smoke and moves a weekly type daily.
- Recommendation: count only a type's own `versions.lock` (skip `images/cloud/<parent>/versions.lock`), and test that a lock-only diff plans that type alone.
- Status: open

### r3.f11 - The pin table and two docs point at places the locked build doesn't read

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `specs/gcp.md:605,613` at c20c32a (with `images/cloud/base/Dockerfile:14-15`, `images/cloud/docker-bake.hcl:48-56`, `scripts/cloud/lock.sh:225-244`, `README.md:89`, `specs/image-toolsets.md:104`)
- Evidence: the base row says pin with `FROM mirror.gcr.io/library/ubuntu:24.04@sha256:...`, but base is `FROM ${BASE_IMAGE}` and lock.sh reads docker-bake.hcl's `BASE_IMAGE` (and its `# pinned:` note there), as gcp.md's own lock table says. The release-tool row says "Its version in the Dockerfile"; tflint and VirtualGL are `${TFLINT_VERSION:-latest}` and `${VIRTUALGL_VERSION:-latest}` in their install.sh, and bake-args overrides any Dockerfile default. README.md:89 and image-toolsets.md:104 still say "newest stable at build time".
- Verified: confirmed against the files; following either row pins nothing.
- Recommendation: rewrite the base row as `BASE_IMAGE = "...@sha256:..."  # pinned: <URL>` in docker-bake.hcl, the release row as `TFLINT_VERSION` / `VIRTUALGL_VERSION` in each install.sh, and say in README and image-toolsets.md that Cloud Run types take versions from their lock.
- Status: open

### r3.f12 - The pins summary can't track apt, Homebrew or Claude `stable` pins as gcp.md says

- Severity: low
- Dimension: drift
- Kind: decision
- Location: `scripts/cloud/lock.sh:248-256,288` at c20c32a (with `lock.sh:141-149,291-308`, `specs/gcp.md:606-617`)
- Evidence: gcp.md says each pin carries `# pinned: <issue URL>`, the lock marks it, and the watch shows the issue's state. apt: `packages_of` strips comments before lock.sh:288 writes `pinned apt/<name> -`, so the URL never arrives. Homebrew: `resolve_brew` writes no `pinned` line. Claude `stable`: the pin table calls it a pin and says "the watch then skips Claude", but it's resolved each run and gets no `pinned` line.
- Verified: confirmed for all three.
- Recommendation: fix apt (read the note from the packages.txt line before stripping, as `pin_note` does for install.sh). For Homebrew and Claude `stable`, either mark them (a `# pinned:` note emitted as a `pinned` line) or correct gcp.md: Homebrew pins aren't tracked, and `stable` is a channel the watch follows. Recommended: fix apt, and correct gcp.md for the other two.
- Status: open

## tests

### r3.f13 - The propose job's link refusal has no test

- Severity: medium
- Dimension: tests
- Kind: fix
- Location: `scripts/cloud/lock-watch.sh:322` at c20c32a (with `.github/workflows/lock-watch.yml:160-192`, `tests/unit/test_lock_watch.py:488-498`)
- Evidence: the candidate comes from the resolve job, which runs upstream code (taps' Ruby, base images), to the propose job, which holds the bot's token. `cmd_guard` checks names only; line 322's `find ... -not -type d -not -type f` is all that stops `commit_on` from committing what a link named like a lock points at. `test_guard_runs_before_any_call` plants only badly named regular files.
- Verified: line 322 replaced with `:` leaves test_lock_watch at 42 OK. With FakeGitHubBase, a lock that's a link to a file holding `GH_TOKEN=ghs_leak` is committed with the mutation and refused ("link or device") without it. A leaked bot token (contents and pull request write; the ruleset needs no approval) could open an auto-merging pull request.
- Recommendation: add ProposeTests cases with the lock as a symlink out of the tree and as a FIFO; assert exit 1, "link or device", and no call in the fake gh's log.
- Status: open

### r3.f14 - Nothing checks that the lock's toolchain versions reach the build

- Severity: medium
- Dimension: tests
- Kind: fix
- Location: `images/cloud/linux/Dockerfile:33,40,41` and `images/cloud/linux-gpu/Dockerfile:18` at c20c32a (with `scripts/cloud/lock.sh:373-375`, `tests/unit/test_bake_script.py:64-76`, `scripts/cloud/record-check.sh`)
- Evidence: bake-args' `RUST_TOOLCHAIN`, `TERRAFORM_VERSION`, `TFLINT_VERSION` and `VIRTUALGL_VERSION` reach install.sh only through `ARG` lines; without one, BuildKit ignores the `--set` and install.sh takes `stable` or `latest`, and the layer's cache key stops following the lock. Tests check the `--set` values; record-check compares toolset names, not versions. Only `APT_SNAPSHOT` is guarded (`:?` in base/Dockerfile:29).
- Verified: deleting `ARG RUST_TOOLCHAIN` or `ARG VIRTUALGL_VERSION` leaves the full suite at 394 OK each (with buildx). A new type's Dockerfile (3d-gpu is planned) is the likely way in.
- Recommendation: a test that, for each Dockerfile running `install-toolset.sh <toolset>`, asserts the toolset's lock-fed `ARG`s appear earlier in the same stage; better, have the PR smoke compare the versions report with the type's lock.
- Status: open

### r3.f15 - cloud-build's `changes` filter and `gate` verdict are untested

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `.github/workflows/cloud-build.yml:44,178-180` at c20c32a (with `tests/unit/test_lock_watch.py:670-677`)
- Evidence: main's ruleset and the lock bot's auto-merge rely on `gate`; `test_gate_reports_on_every_pull_request` checks the trigger, `needs` and `if: always()`, not the regex or the verdict.
- Verified: dropping `cloud|` from the regex (a lock-only PR is then "not relevant" and `gate` passes) and `&&` to `||` in the verdict each leave test_lock_watch and test_bake_script green. The deploy's `-next` smoke still stands before promote.
- Recommendation: move the path decision and the verdict into a script, as lock-watch.sh does, and test a lock-only diff, a specs-only diff and each non-success of `check` and `build`.
- Status: open

### r3.f16 - The lock's pin lines are untested, and a Claude pin's issue URL is lost

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `scripts/cloud/lock.sh:86-95,100,144,207` at c20c32a (with `tests/unit/test_lock.py:356-364`)
- Evidence: the pin tests never assert the `pinned` line that `lock-watch.sh pins` lists. The gap hides a bug: gcp.md says the `# pinned:` note goes where the dependency is declared, which for Claude is `default = "…"` inside `variable "CLAUDE_VERSION"`, but `pin_note` reads only the `variable` line and the line above it.
- Verified: with all 22 ResolveTests running, dropping `pinned claude` (:144), the `~>` pinned terraform line (:207), `pin_note`'s line-above branch (:90) or bake_print's `env -u` (:100) each stays green. A note on the `default` line, or on a comment inside the block, gives `pinned claude -`.
- Recommendation: assert the `pinned <what> <URL>` line in each pin test, add a case with `CLAUDE_VERSION` set in the environment, and make `pin_note` read the variable's block for the bake file (or document where the note must sit).
- Status: open

### r3.f17 - The apt resolve's container script never runs in a test

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `scripts/cloud/lock.sh:268-275` at c20c32a (with `tests/unit/test_lock.py:49-74,155-160`)
- Evidence: FAKE_DOCKER checks the mounts and environment, then prints `FAKE_APT`, already stripped of `Inst ` and without `Conf` lines, so the `-c` script (`apt-get update`'s error path, `--no-install-recommends`, the `Inst` filter) is checked only against the fake. AGENTS.md "Test fakes" asks the fake to reject what the real tool would.
- Verified: replacing the install line with `apt-get install --simulate $PACKAGES` (recommends included, no filter) leaves test_lock OK. Run for real it would lock recommended packages the image never installs.
- Recommendation: have the fake run the `-c` script with a fake `apt-get` first on PATH that prints real `--simulate` output (`Inst`, `Conf`), refuses `install` without `--no-install-recommends`, and fails `update` on request.
- Status: open

### r3.f18 - The lock resolve tests skip in a session and fail with Homebrew's buildx

- Severity: info
- Dimension: tests
- Kind: decision
- Location: `tests/unit/test_lock.py:20,178,224-235` at c20c32a (with `triage.yaml:160-170`, `Makefile.md`)
- Evidence: the 22 ResolveTests skip unless `docker` is on PATH, and the session doctor profile doesn't ask for it, so `make cloud-test` in a runner session is OK with them skipped. With Homebrew's docker and docker-buildx, `run_lock`'s environment (`HOME=tmp`, no `DOCKER_CONFIG`) hides the plugin.
- Verified: 17 failures and 2 errors, all in ResolveTests, after a reviewer brew-installed docker and buildx in this session. Hosted runners keep buildx system-wide, so CI is unaffected.
- Recommendation: skip on `docker buildx version` failing, pass `DOCKER_CONFIG` through to the real docker, and either add docker and docker-buildx to the session doctor profile or say in Makefile.md that these tests need them.
- Status: open

## duplication

### r3.f19 - lock-watch.yml retypes the bot's branch and open-PR query, and two places write locks besides lock.sh

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `.github/workflows/lock-watch.yml:82-89,97-109` at c20c32a (with `scripts/cloud/lock-watch.sh:49,290-294`, `Makefile:355-359`, `scripts/cloud/lock.sh:3-4,68`)
- Evidence: Decide types `lock/watch` four times and repeats `open_pr`'s query; if the name changes in one place, Decide sees no open pull request, skips carry, and the next push drops the weekly candidates. lock.sh says it's the locks' one reader and writer, but the Resolve step and `make cloud-lock WRITE=1` each build the lock path and `mv` into it.
- Verified: confirmed; the copies agree today.
- Recommendation: `lock-watch.sh open` and a `branch` subcommand for Decide; `lock.sh write <type>` for the workflow and the Makefile.
- Status: open

### r3.f20 - Three packages.txt parsers and two Brewfile tap loops

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `scripts/cloud/lock.sh:255,298,316-319` at c20c32a (with `images/toolsets/install-toolset.sh:46,109-117`, `images/cloud/base/Dockerfile:36`)
- Evidence: the lock is right only while it resolves exactly what the build installs. `packages_of` copies install-toolset.sh's sed; the base Dockerfile has a third, already different (no trailing-space strip, harmless only through word splitting). The tap regex and tap/trust loop are copied from install-toolset.sh, as is `brew bundle list --formula`.
- Verified: confirmed; a divergence would fail loudly rather than silently.
- Recommendation: one reader (`install-toolset.sh --list-packages|--list-taps <name>…`, or a sourced helper beside it) used by lock.sh and the base Dockerfile.
- Status: open

### r3.f21 - Release tools are listed in four places in lock.sh, and each repository twice

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `scripts/cloud/lock.sh:210,213-220,345-347,422,435` at c20c32a (with `images/toolsets/gpu/install.sh:28-38`, `images/toolsets/terraform/install.sh:18-20`)
- Evidence: a new release tool means editing `resolve_terraform`/`cmd_resolve`, `release_repo`, `pinned_value` and `upstream_newest`; `VirtualGL/virtualgl` and `terraform-linters/tflint` are spelled in lock.sh and in their install.sh, and gpu/install.sh repeats `github_latest`'s redirect lookup. A moved repository has to be fixed twice, or the lock resolves a tag from one repository while the build downloads from another.
- Verified: confirmed; partly unavoidable, since the build container and the resolver are different contexts.
- Recommendation: declare each repository once beside its constraint in install.sh (`TFLINT_REPO=...`, used by its URLs), read it with a `declared`-style reader, and drive lock.sh's lists from one table.
- Status: open

### r3.f22 - Claude's `latest` default and release URL are restated outside docker-bake.hcl

- Severity: info
- Dimension: duplication
- Kind: decision
- Location: `scripts/cloud/bake.sh:29` at c20c32a (with `Makefile:118`, `images/cloud/docker-bake.hcl:28-31`, `scripts/cloud/lock.sh:57`, `.github/workflows/cloud-deploy.yml:111`)
- Evidence: lock.sh treats docker-bake.hcl's `CLAUDE_VERSION` default as the constraint, but bake.sh and the Makefile restate `latest` and always export it, so a bake-file pin never reaches the orchestrator's local or PR build; the deploy curls the same release URL lock.sh holds.
- Verified: lowered to info: the orchestrator taking `latest` everywhere is by design (cloud-build.yml:119, cloud-deploy.yml:106-108), so it's a copy, not drift.
- Recommendation: decide whether the orchestrator follows a Claude pin. If not, say so beside the bake variable and drop bake.sh's redundant default; if so, have the deploy and bake.sh ask lock.sh for the version.
- Status: open
