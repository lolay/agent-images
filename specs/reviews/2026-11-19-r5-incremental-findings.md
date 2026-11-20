# r5 incremental review: findings

- Kind: incremental
- Range: bb8929cf9148751b05d17a71e2daadaa9ed691d1..1a7f6abbfa443c266e9c89cb635ad408a331535e
- Repo: lolay/agent-images
- Follows: r4
- Scope: 29 commits, #107 to #136 (r4's record and remediation, apt snapshot pins, the new-type lock in cloud-build, release tools read from their toolsets, the bot's lock bumps); about 4,800 changed lines, the generated `versions.lock` files read only as data
- Reviewers: correctness (`/code-review xhigh`), drift, tests, duplication, fix review (two passes); Opus, high, fresh context
- Verifier: 18 candidates; dropped 1 (`lock-watch.sh types` as dead code: it's the test seam for the class mapping and the timeout budget), folded 2 into open findings (the lock path re-spelled into r3.f19, the list-line readers into r3.f20), lowered 3 (f3, f4, f16)
- Accepted, not raised again: r2.f16

## correctness

### r5.f1 - Blender's tarball and its SHA-256 can come from the same third-party mirror

- Severity: medium
- Dimension: correctness
- Kind: decision
- Location: `images/toolsets/3d/blender.sh:18-19,23-27,40-53` at 1a7f6ab (with `images/toolsets/3d/install.sh:46-50`, `scripts/cloud/lock-watch.sh:389-393`)
- Evidence: `get` tries download.blender.org, then mirrors.ocf.berkeley.edu, then ftp.nluug.nl, separately for each file. install.sh fetches the tarball (:46) and `blender-<version>.sha256` (:47) through it and checks one against the other (:48-50). When blender.org refuses the runner, the case the fallback exists for, both files can come from the same mirror, and `sha256sum -c` catches only a corrupt download, not a modified one. The header (:18-19) presents the check as an integrity guarantee. At bb8929c both files came over HTTPS from download.blender.org only, and the lock records Blender's version, not a hash (`images/cloud/3d-gpu/versions.lock:531`).
- Verified: with a fake `curl` that refuses blender.org, `blender.sh get Blender5.3/blender-5.3.0.sha256` is served from mirrors.ocf.berkeley.edu; the tarball takes the same path. Any build on `main` that falls back is exposed, and lock pull requests merge automatically. Medium at its upper edge: an attacker needs an official HTTPS mirror.
- Recommendation: options: (a) the lock records the release's SHA-256 at resolve (`release-sha256 blender ...`, read from blender.org), and install.sh checks the tarball against it, so only the tarball falls back (recommended); (b) fetch the `.sha256` and the index from download.blender.org only, and let only the tarball fall back; (c) drop the mirrors and accept an occasional failed build.
- Status: accepted
- Reason: the mirrors are official university mirrors over HTTPS; the residual risk of a compromised mirror serving a matching tarball and `.sha256` is accepted (Owner, 2026-11-19, lolay/agent-images#138)
- Resolution: decided on #138 (issuecomment-7020006973); lolay/agent-images#140 rewords `blender.sh`'s header to say the checksum catches corruption, not origin

### r5.f2 - `blender.sh latest` can read a lagging mirror's index and lock an older Blender

- Severity: low
- Dimension: correctness
- Kind: fix
- Location: `images/toolsets/3d/blender.sh:55-64` at 1a7f6ab (with `scripts/cloud/lock.sh:329-339`)
- Evidence: `latest` reads both index pages through `get`, so when blender.org refuses, the newest version is whatever the first answering mirror lists. `resolve_release` writes that tag as is (:332-335) and never compares it with the lock's `release blender` line, so a mirror that hasn't synced proposes a downgrade, which auto-merges and is undone by the next run that reaches blender.org: two 3d-gpu rebuilds with the older Blender between.
- Verified: with blender.org refused and a mirror index listing only `Blender5.2/`, `blender.sh latest` prints `5.2.9`; the lock has `release blender 5.3.0`.
- Recommendation: no mirror fallback for `latest` (since #114 a failed resolve skips only that type), or refuse a newest that sorts below the locked version.
- Status: fixed
- Resolution: lolay/agent-images#142 (dd935fc), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f3 - `CUDA_CAPABILITIES` order isn't checked, but PTX goes to the last entry

- Severity: info
- Dimension: correctness
- Kind: fix
- Location: `images/cloud/docker-bake.hcl:98,106-127,140-141` at 1a7f6ab (with `images/toolsets/gpu/install.sh:52-58`)
- Evidence: `cmake_cuda_archs` marks every entry but the last `-real` (:121), and `torch_cuda_arch_list` appends `+PTX` to the joined list, so both put PTX on the last entry; gpu/install.sh builds PTX from the one entry without `-real`. "Oldest first" is only a comment (:98); the validation (:112) accepts any order. An override of `"12.0 8.9"` yields `CUDAARCHS=120-real;89` and PTX for compute_89, and every `sm_` check passes.
- Verified: read both functions; no Docker for `bake --print`. Lowered to info: both GPUs still get SASS, and a newer GPU still JITs from compute_89 PTX; only newest-architecture PTX is lost.
- Recommendation: sort the capabilities numerically in both functions, or reject a list that isn't ascending.
- Status: fixed
- Resolution: lolay/agent-images#143 (e615212), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

## tests

### r5.f4 - test_toolset_args can't see a locked version read by a helper script install.sh runs

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `tests/unit/test_toolset_args.py:21,36-50` at 1a7f6ab (with `scripts/cloud/lock.sh:285-305`, `images/toolsets/3d/install.sh:41,46-47`)
- Evidence: `fed_by` reads `install.sh`, or the one script a Dockerfile runs directly. Since #125, `release_tools` locks a `${NAME_VERSION:-...}` from any `images/toolsets/<toolset>/*.sh` (lock.sh:292), and install.sh now runs helpers beside it (`bash "$here/blender.sh"`). A helper that reads a locked version gets a `release` line and a bake `--set` but no Dockerfile `ARG`, so the build takes the newest release while the lock records another: r4.f1's failure, one level down. r4.f1's recommendation (read every script in the toolset) wasn't taken.
- Verified: in a copy, a `gpu/fetch-xtool.sh` with `${XTOOL_VERSION:-latest}` and `XTOOL_RELEASES=`, run from gpu/install.sh, with `release xtool 1.0.0` in both GPU locks: `lock.sh releases linux-gpu` lists it, bake-args prints `*.args.XTOOL_VERSION=1.0.0`, no ARG exists, and `test_toolset_args` stays green; the same two lines in install.sh turn it red. Lowered to low: no helper reads a locked version today.
- Recommendation: build `fed_by`'s file set the way lock.sh does, ideally from `lock.sh releases <type>` (its second column), so one source says which file declares which tool; add a fixture with the declaration in a helper.
- Status: fixed
- Resolution: lolay/agent-images#142 (dd935fc), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f5 - release_latest's tag check and script-name check have no test

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `scripts/cloud/lock.sh:317,323` at 1a7f6ab (with `tests/unit/test_lock.py:1029`)
- Evidence: both guards are new. :323 refuses a newest release that isn't one token; without it a source printing junk writes `release <name> 7.1.0 <html>` to the lock and bake-args passes it on. :317 limits a script source to a plain name beside the declaring file; `test_an_unknown_source_fails` tries only `missing.sh`, so an existing `../x.sh` would run.
- Verified: replacing :323's regex with `-n`, and reducing :317 to the existence check, leaves `test_lock` (81 run, 44 skipped) and the rest of the suite green.
- Recommendation: in the release-tool tests, which need no Docker, add a `latest.sh` that prints two words and a `../`-style source that exists.
- Status: fixed
- Resolution: lolay/agent-images#142 (dd935fc), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f6 - The GPU builder scripts' new logic has no test, and check.txt can't see it break

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `images/toolsets/gpu/install.sh:54-59`, `images/toolsets/3d/build-colmap.sh:38-42,62-91` at 1a7f6ab (with `images/toolsets/gpu/check.txt`, `tests/unit/test_install_toolset.py:295-320`)
- Evidence: gpu/install.sh now turns `CUDAARCHS` into nvcc `-gencode` flags (SASS per GPU, PTX for the entry without `-real`, :58), and build-colmap.sh writes its runtime list (`name=version` for what the build list installed, bare names for the base's, a `die` on no owners). The unit tests stop before either (at the `github_latest` failure and at "no packages"), and check.txt lists SASS only (`cuobjdump --list-elf`).
- Verified: deleting the PTX arm, making the input check `true`, dropping `${built:-...}`, and `true || die` on the owners check each leave the full suite green (542 run, 46 skipped).
- Recommendation: move the `CUDAARCHS` to `-gencode` translation into a function or a `--print-gencode` mode and test `89-real;120`, a `-virtual` entry and a bad entry; add `cuobjdump --list-ptx ... compute_120` to gpu/check.txt; test the runtime list with fake `dpkg-query` and `ldd`, or record that it's checked only in the image.
- Status: fixed
- Resolution: lolay/agent-images#142 (dd935fc), per its fix claim; follow-up #146 tightened its `dpkg-query` fake. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

## drift

### r5.f7 - image-toolsets.md still writes the GPU target lists r4.f9 replaced, and now they're wrong

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `specs/image-toolsets.md:241-243` at 1a7f6ab (with `images/cloud/docker-bake.hcl:104-105`, `images/toolsets/3d/build-colmap.sh:55`)
- Evidence: the spec says COLMAP builds with `CMAKE_CUDA_ARCHITECTURES="89;120"` and gsplat with `TORCH_CUDA_ARCH_LIST="8.9;12.0+PTX"`. Since #126 build-colmap.sh passes `$CUDAARCHS`, which docker-bake.hcl derives as `89-real;120` (SASS only for 8.9). The same range edited this paragraph. The bake file's comment (:104-105) calls a `CUDA_CAPABILITIES` edit "the image side of a new GPU type", but it also needs `sm_` lines in both check.txt files and an edit to `test_the_list_is_the_l4_and_blackwell`.
- Verified: at bb8929c the build passed `"89;120"`; at 1a7f6ab it doesn't.
- Recommendation: say both builds read `CUDAARCHS` and `TORCH_CUDA_ARCH_LIST` from base-gpu, as :366-370 already does; list the check.txt lines and the test in the bake comment.
- Status: fixed
- Resolution: lolay/agent-images#140 (b1369fb), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f8 - apt-pins.sh and the base Dockerfile say APT_PINS comes from the lock; it comes from the package lists

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `images/cloud/base/apt-pins.sh:17-18`, `images/cloud/base/Dockerfile:27`, `specs/gcp.md:308-309` at 1a7f6ab (with `scripts/cloud/lock.sh:559-566,582,586`, `specs/gcp.md:349,380-385`)
- Evidence: apt-pins.sh says bake-args gives the build `APT_PINS` "from the lock's apt-pin-snapshot lines", and the Dockerfile says "APT_PINS is the lock's pins". `cmd_bake_args` builds it from the type's package lists and skips `apt-pin-snapshot` lines (lock.sh:582); lock.sh:559-561 and gcp.md:380-385 say so, deliberately, so a pin builds on its pull request before the watch re-locks. A reader of apt-pins.sh concludes the opposite. Separately, gcp.md:308-309 lists lock.sh's subcommands without `releases` (#132), which gcp.md:349 relies on.
- Verified: read `cmd_bake_args` and its case arm.
- Recommendation: say APT_PINS comes from the package lists and the lock's `apt-pin-snapshot` lines are a record; add `releases` to gcp.md:308.
- Status: fixed
- Resolution: lolay/agent-images#140 (b1369fb), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f9 - gcp.md's "Adding a type" leaves out the first-lock step

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `specs/gcp.md:95-107` at 1a7f6ab (with `specs/gcp.md:363-372`, `AGENTS.md:34-41`, `scripts/cloud/new-lock.sh:62-64`)
- Evidence: the checklist ends "The pull request builds it with the other types". Since #113, a new type's first cloud-build resolves its lock, uploads it as `versions-lock-<type>` and fails; the type builds only after the author commits the downloaded file. That step is in gcp.md:363-372 and AGENTS.md, not in the checklist.
- Verified: read new-lock.sh's exit path and the checklist.
- Recommendation: add the step (fetch the artifact with the job summary's command, commit it unedited, push) and move "builds it" after it.
- Status: fixed
- Resolution: lolay/agent-images#140 (b1369fb), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f10 - `make cloud-lock WRITE=1` writes a lock, though the docs now say none is written by hand

- Severity: low
- Dimension: drift
- Kind: decision
- Location: `Makefile:378-386`, `Makefile.md:202,212` at 1a7f6ab (with `specs/gcp.md:370-372`, `AGENTS.md:28`)
- Evidence: the range added gcp.md:370-372, "no lock is written or seeded by hand", and rewrote the target's comment to say WRITE=1 is "for checking a constraint change on a host with Docker before its pull request". The target still `mv`s into `images/cloud/<TYPE>/versions.lock` (:386), and Makefile.md says "WRITE=1 writes it". r3.f19 is the duplicate writer; this is the policy contradiction.
- Verified: the diff alone, which the target prints without WRITE, checks a constraint change. WRITE=1 has a real use: bake-args overrides a release pin with the lock's value, so a local build of a pin change needs the lock rewritten.
- Recommendation: options: (a) keep WRITE=1 and say in the Makefile, Makefile.md and gcp.md that it's for a local `make cloud-build` against a candidate lock and is never committed (recommended); (b) drop WRITE and keep `cloud-lock` a read-only diff; (c) route it through r3.f19's `lock.sh write`.
- Status: fixed
- Resolution: lolay/agent-images#140 (b1369fb), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f11 - The toolset contract doesn't say install.sh runs with install-toolset.sh's helpers

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `specs/image-toolsets.md:47,52-60` at 1a7f6ab (with `images/toolsets/install-toolset.sh:21-28,171-176`)
- Evidence: since #126, the 3d, gpu, gis, android and rust install.sh call `log`, `die`, `apt`, `install_apt`, `install_apt_packages` and `github_latest`, which only install-toolset.sh's subshell exports, so an install.sh no longer runs on its own, and a builder-stage script sources install-toolset.sh for them. The spec still says "one install script" (:47) and install.sh is "anything else" (:56); the helper contract is only in install-toolset.sh's header.
- Verified: narrowed by the verifier: the release declarations (gcp.md:342-350) and the builder stage (image-toolsets.md:57-58,242-249) are documented.
- Recommendation: a few lines in the toolset layout naming the helpers, that install.sh and scripts beside it run under install-toolset.sh, and that a builder script sources it.
- Status: fixed
- Resolution: lolay/agent-images#140 (b1369fb), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

## duplication

### r5.f12 - Two `# pinned:` note readers in lock.sh disagree

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `scripts/cloud/lock.sh:118-153` (regex :121) and `:399-420` (regex :408) at 1a7f6ab
- Evidence: the convention that a `# pinned: <URL>` note names a pin's issue (gcp.md, "Pinning a dependency") is read by `pin_note` for toolset scripts and the bake file (`/# pinned: [^ ]+/`) and by `apt_pin_notes` for package lists (`#[[:space:]]*pinned:[[:space:]]*(...)`), added by #121. On `#pinned: X`, `# pinned:<tab>X` and `#  pinned: X`, `pin_note` gives `-` and `apt_pin_notes` gives X. The list-line readers beside them are r3.f20's.
- Verified: ran both expressions on the four spellings; they agree only on `# pinned: X`.
- Recommendation: one `pin_issue <line>` reader that both call.
- Status: fixed
- Resolution: lolay/agent-images#141 (6379bb9), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f13 - The build's apt bootstrap is written twice, and the copies already differ

- Severity: low
- Dimension: duplication
- Kind: decision
- Location: `images/cloud/base/Dockerfile:39-46`, `scripts/cloud/lock.sh:442-463` at 1a7f6ab (with `images/toolsets/install-toolset.sh:53`)
- Evidence: both run `apt-get update`, ca-certificates from today's archive, `apt-pins.sh install $APT_PINS`, `apt-get update`, then the install, and #116 extended both. The build installs with `--allow-downgrades`; the resolve simulates without it, and runs the ca-certificates step only when there are pins. The lock is right only while it resolves what the build installs; only a comment (lock.sh:442-445) links them.
- Verified: read both sequences.
- Recommendation: options: (a) one `apt-prepare.sh [--simulate]` beside apt-pins.sh that the base RUN and the resolve's container both call (recommended); (b) record in gcp.md that the two are kept in step by hand, naming the flags that must match.
- Status: fixed
- Resolution: lolay/agent-images#141 (6379bb9), per its fix claim; follow-up #145 routes its `apt-get update` steps through `apt_update`. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f14 - terraform's tflint `latest` bypasses `github_latest`, and #133's guard can't see it

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `images/toolsets/terraform/install.sh:20-27` at 1a7f6ab (with `images/toolsets/install-toolset.sh:67-75`, `tests/unit/test_install_toolset.py:205-218`)
- Evidence: #133 made one `/releases/latest` lookup the rule. With `TFLINT_VERSION` unset, terraform/install.sh downloads `$TFLINT_RELEASES/latest/download/...` twice (the zip, then `checksums.txt`), with no tag check; the lock reads tflint through `github_latest`. The guard greps only for `url_effective`. Not live on Cloud Run (the ARG and the lock set the version); live for the VM images and `scripts/anthropic/setup.sh:19`. A release landing between the two downloads fails loudly.
- Verified: read the script and the guard.
- Recommendation: resolve the tag with `github_latest` and download from `/download/<tag>`, as gpu/install.sh:31-37 does; widen the guard to any `releases/latest` URL outside install-toolset.sh.
- Status: fixed
- Resolution: lolay/agent-images#143 (e615212), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f15 - Four install.sh scripts print install-toolset.sh's log line by hand, now under the wrong name

- Severity: info
- Dimension: duplication
- Kind: fix
- Location: `images/toolsets/terraform/install.sh:33`, `rust/install.sh:21`, `android-go/install.sh:18`, `android-rust/install.sh:16` at 1a7f6ab (with `images/toolsets/install-toolset.sh:39`)
- Evidence: each does `printf '==> install-toolset: ...'`. Since #126 every install.sh gets `log` with `TOOLSET_LOG_NAME` set to its toolset, so these restate log's format and name the wrong toolset. r4.f13's fix covered the 3d and gpu scripts it named; the guard (`test_install_toolset.py:188`) matches only function definitions.
- Verified: at bb8929c `log` printed the same prefix, so these matched; now they don't. Cosmetic.
- Recommendation: `log "..."` in each; have the guard also flag a literal `'==> ` in a toolset script.
- Status: fixed
- Resolution: lolay/agent-images#143 (e615212), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code

### r5.f16 - Each workflow that runs `lock.sh resolve` puts Homebrew on PATH itself

- Severity: info
- Dimension: duplication
- Kind: fix
- Location: `.github/workflows/cloud-build.yml:138-139`, `.github/workflows/lock-watch.yml:82-83` at 1a7f6ab (with `scripts/cloud/lock.sh:505-513`)
- Evidence: `lock.sh resolve` needs `brew`, but leaves finding it to the caller; cloud-build's new Lock step copies lock-watch.yml's comment and `eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"` word for word.
- Verified: lowered to info: a drifted copy fails loudly at `prepare_brew`'s `brew update` under `set -e`.
- Recommendation: `prepare_brew` puts Homebrew on PATH when `brew` isn't found; drop the line from both workflows.
- Status: fixed
- Resolution: lolay/agent-images#143 (e615212), per its fix claim. Marked fixed by the Owner's call (2026-11-20) without a fix review against the code
