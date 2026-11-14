# r4 incremental review: findings

- Kind: incremental
- Range: c20c32aed583b05e007f994a05006e589189bfb4..bb8929cf9148751b05d17a71e2daadaa9ed691d1
- Repo: lolay/agent-images
- Follows: r3
- Scope: 25 commits, #81 to #106 (m3's GPU types, r3's remediation, lock bumps); `images/toolsets/3d/uv.lock` (generated) left out
- Reviewers: correctness, drift, tests, duplication, fix review; Opus, high, fresh context
- Verifier: 22 raw findings merged to 15 candidates; dropped 1 of 15 (the watchdog's path prefix check: no trust boundary, since only the session's own user can serve the status it reads)
- Accepted, not raised again: r2.f16

## tests

### r4.f1 - The lock's COLMAP_VERSION can miss the build with every test green

- Severity: medium
- Dimension: tests
- Kind: fix
- Location: `tests/unit/test_toolset_args.py:18,33-51,66-70`, `images/cloud/3d-gpu/Dockerfile:11-14` at bb8929c (with `images/toolsets/3d/build-colmap.sh:20-24`)
- Evidence: `fed_by` reads only `images/toolsets/<toolset>/install.sh`, and `violations` checks only stages with an `install-toolset.sh <name>` line. The `colmap` builder stage runs `build-colmap.sh` through a bind mount, and that script reads `${COLMAP_VERSION:-latest}`. `test_the_derivation_finds_the_toolchains` expects `"3d": {"BLENDER_VERSION"}`, writing the gap in.
- Verified: in a copy of bb8929c with `ARG COLMAP_VERSION` deleted, `test_toolset_args` passes (3 tests); with `ARG BLENDER_VERSION` deleted it fails. Without the ARG, BuildKit ignores bake's `--set`, so the build compiles COLMAP's newest release while the lock says 4.2.1: r3.f14's failure, on the new type.
- Recommendation: derive readers from every script in the toolset's directory, and treat any `RUN` that bind-mounts or runs one (`source=images/toolsets/<name>/...`) as using that toolset. Expect `{"BLENDER_VERSION", "COLMAP_VERSION"}` for 3d, and watch the test go red with the ARG removed.
- Status: open

### r4.f2 - No test deploys the real 3d-gpu.env, so dropping its LAUNCH_STAGE stays green

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `infra/gcp/3d-gpu.env:18-21` at bb8929c (with `tests/unit/test_deploy.py:149-163`, `tests/unit/test_cloud_lib.py:145-146`, `tests/unit/fake_gcp.py:326-337`)
- Evidence: fake_gcp refuses a GPU job with a task timeout over an hour unless it declares ALPHA or BETA (#94), but only `test_linux_gpu_deploys_on_an_rtx_pro_6000` sends a real env file through it. `test_cloud_lib` runs every real env file through `load_compute_type`, which doesn't know the rule.
- Verified: with `LAUNCH_STAGE=BETA` removed from `3d-gpu.env`, the whole suite passes (426 tests); the same edit to `linux-gpu.env` fails two. The pull request check writes no job, so the gap first shows as the push deploy's 400 at the `-next` patch, before promote.
- Recommendation: deploy every Cloud Run type in `infra/gcp` through the fake, a subTest per type (skipping `DEPLOY=false`), and keep the RTX PRO 6000 limits assertion for the GPU types. Or have `job-spec.sh` derive `BETA` from a GPU and a timeout over 3600 s, so no env file has to restate it.
- Status: open

### r4.f3 - The carry test doesn't check that an untouched lock is still carried

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `scripts/cloud/lock-watch.sh:116`, `tests/unit/test_lock_watch.py:404-409` at bb8929c
- Evidence: r3.f1's fix drops a carried lock only when `git diff --quiet "$base" "$main" -- "$path"` fails. `test_carry_ignores_what_main_changed_since` moves `linux-gpu` on main and asserts only that it isn't carried.
- Verified: with `-- "$path"` removed, so any change on main drops every carried lock, `test_lock_watch` passes (45 tests). Any merge to main would then make the next hourly run drop the weekly run's candidates.
- Recommendation: in that test, also assert that `gis/versions.lock` holds the branch's version and that stderr says `carried images/cloud/gis`.
- Status: open

### r4.f4 - `make doctor`'s MODE to profile mapping is untested

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `Makefile:150-159` at bb8929c (with `tests/unit/test_session_gate.py:98-104`, `triage.yaml:167-185`)
- Evidence: #96's tests cover cloud-lint and pre-commit per MODE and the profiles' text; nothing runs `make doctor`. The Makefile says the gate "matches make doctor's profile for the same MODE".
- Verified: swapping `android) profile=session-android` to `session` keeps `test_session_gate` green (9 tests); doctor's MODE refusal is untested. The session profile also omits `jq`, which cloud-lint runs, but `core` installs it in every image, so that half is minor.
- Recommendation: run `make doctor AGENT_TYPE=linux [MODE=...]` with a fake `triage` on PATH that records `--profile`; assert `session`, `session-android`, and a refusal for anything else.
- Status: open

### r4.f5 - lock.sh's new colmap and blender pin arms have no test

- Severity: low
- Dimension: tests
- Kind: fix
- Location: `scripts/cloud/lock.sh:447-453` (arm :450), `:457-470` (arm :463) at bb8929c
- Evidence: #103 added `colmap | blender` to `pinned_value` and `upstream_newest`. The pins tests cover claude, rust, tflint and apt; the only colmap and blender test is a resolve test (`test_lock.py:322-327`). Extends r3.f16.
- Verified: with either arm removed, `test_lock*.py` passes (74 tests). Without the `upstream_newest` arm, `pins` dies with "no upstream known" on a pinned blender, failing the whole pins list.
- Recommendation: add a `release blender` and `pinned blender -` row to `test_each_pin_with_upstream_and_its_issue`, with blender.org's index in its URLs. Deriving the tool list once (r4.f12) removes the arms.
- Status: open

## drift

### r4.f6 - The docs say `/opt/3d` is root-owned; it's the session user's

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `specs/gcp.md:1201,1484`, `tests/sessions/3d-gpu.md:110-113` at bb8929c (with `images/toolsets/3d/install.sh:63-69`, `images/cloud/base/Dockerfile:41`)
- Evidence: #105 and #106 recorded "`/opt/3d` is root-owned, so they go in with `uv pip install --target`", and the session test says sessions can't write to it. `install.sh` runs as `ubuntu` (`USER ubuntu`, base/Dockerfile:41), creates the directory with `sudo install -d -o "$(id -un)"` and runs `uv sync` as that user, as `gis` does for `/opt/gis`.
- Verified: no later stage changes `USER` or chowns `/opt`, and `job-spec.sh` sets no `runAsUser`, so the container runs as uid 1000, which owns `/opt/3d` and its `.venv`.
- Recommendation: correct both docs, and redecide the open item: a session can `uv pip install --python /opt/3d/.venv` into it directly (the `--target` route works but isn't needed), and the toolset's `CLAUDE.md` line can say so.
- Status: open

### r4.f7 - m3's close left README and two env files pointing at what's gone

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `README.md:50-51`, `infra/gcp/linux-gpu.env:12`, `infra/gcp/3d-gpu.env:12` at bb8929c
- Evidence: gcp.md says m3 is done, but README's type table still says `Planned (m3)` for `linux-gpu` and `3d-gpu`. #106 deleted `specs/handoffs/infra-ask-m3-gpu.md`, which both GPU env files cite for the quota.
- Verified: the handoff is gone from `specs/handoffs/`; its facts are in `specs/gcp-infrastructure.md` "GPU quota".
- Recommendation: set README's two rows to running, and point both env files at gcp-infrastructure.md "GPU quota".
- Status: open

### r4.f8 - gcp.md's lock tables don't know COLMAP, Blender or the 3d uv.lock

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `specs/gcp.md:326,328-332,618`, `Makefile.md:202` at bb8929c (with `scripts/cloud/lock.sh:38-40,247`)
- Evidence: #103 taught the lock two release sources: COLMAP from GitHub, read from `build-colmap.sh`, and Blender from blender.org's index, read from `3d/install.sh`. gcp.md's lock table has one release row (tflint, VirtualGL from GitHub), the bake paragraph lists only those build args, and Makefile.md says cloud-lock resolves "the GitHub releases". gcp.md "The lock" doesn't say `/opt/3d` sits in a second lock the watch doesn't move (only image-toolsets.md:242-243 does).
- Verified: read at bb8929c. The pin table's release row ("its version in the Dockerfile") is wrong for every release tool, which is r3.f11's.
- Recommendation: add both tools to the lock table and bake paragraph, fix Makefile.md and lock.sh:247's comment, and add one line under "The lock" naming `images/toolsets/3d/uv.lock` as upgraded by hand.
- Status: open

### r4.f9 - The GPU target list is written three ways, and only sm_120 is checked

- Severity: low
- Dimension: drift
- Kind: fix
- Location: `images/toolsets/3d/build-colmap.sh:51`, `images/toolsets/3d/install.sh:89`, `images/toolsets/gpu/install.sh:56-62`, `images/toolsets/3d/check.txt:8,12` at bb8929c (with `specs/gcp.md:1481`)
- Evidence: the L4 and Blackwell targets are `CMAKE_CUDA_ARCHITECTURES="89;120"`, `TORCH_CUDA_ARCH_LIST="8.9;12.0+PTX"` and three nvcc `-gencode` lines. gcp.md's open items say "the images already carry `sm_89`, so an L4 type is an env file".
- Verified: check.txt greps only `sm_120`; dropping 89 from any one build passes every check and fails only as "no kernel image" on an L4.
- Recommendation: declare the list once (a bake arg on `base-gpu` turned into `CUDAARCHS` and `TORCH_CUDA_ARCH_LIST`, which CMake and PyTorch read, with gpu/install.sh building its flags from it), and add `sm_89` checks beside the `sm_120` ones.
- Status: open

### r4.f10 - The 3d toolset installs apt packages and needs a builder stage outside the toolset contract

- Severity: low
- Dimension: drift
- Kind: decision
- Location: `images/toolsets/3d/install.sh:18,30-39`, `images/toolsets/3d/build-colmap.sh:31-38,71-76` at bb8929c (with `scripts/cloud/lock.sh:270-280`, `specs/gcp.md:321`, `specs/image-toolsets.md:47-50,240-242,361-362`)
- Evidence: the builder stage apt-installs about 25 dev packages, and install.sh installs COLMAP's runtime libraries from a list generated at build. `packages_of` reads only packages.txt, so 3d-gpu's lock records neither, though gcp.md's apt row says the lock holds the full resolved list. install.sh dies without `/opt/colmap` from the type's builder stage, against "a toolset is independent of the base it lands on" and "one RUN per toolset".
- Verified: every install reads `APT_SNAPSHOT`, so the build stays reproducible; what's lost is the lock's record and its "this package moved" signal (a security update to OpenImageIO's codecs moves nothing until another package moves the snapshot). image-toolsets.md describes the builder stage but doesn't amend the contract.
- Recommendation: decide. Either the contract allows a builder stage and a generated apt list (say so in image-toolsets.md "Toolsets" and "Build wiring" and gcp.md's apt row: these ride the snapshot but aren't recorded or pinnable), or list COLMAP's runtime libraries in `3d/packages.txt`, checked against the generated list at build, so the lock records them. Recommended: the second, for the runtime libraries the image ships; the builder's dev packages don't ship.
- Status: open

### r4.f11 - 3d-gpu's lock was seeded by hand, against AGENTS.md

- Severity: low
- Dimension: drift
- Kind: decision
- Location: `images/cloud/3d-gpu/versions.lock:1-12` at bb8929c (with `AGENTS.md` "No Docker in a session", `specs/gcp.md:298-299`)
- Evidence: #102 added to AGENTS.md that a new type's lock is seeded with `make cloud-lock TYPE=<type> WRITE=1` on a host with Docker. #103, one PR later, seeded 3d-gpu's by hand with the seven lines `bake-args` reads, from a session with no Docker; the header says both "Written by scripts/cloud/lock.sh resolve" and "Seeded by hand". gcp.md calls lock.sh the lock's one writer.
- Verified: #103's description disclosed it, and `bake-args` reads only the seeded lines, so the build was right; the record isn't, until the weekly watch resolves the whole lock.
- Recommendation: decide which path a session takes for a new type. Either AGENTS.md documents the minimal hand seed (exactly what `bake-args` needs, marked as such), or the lock watch gets a dispatchable one-type resolve a session can trigger instead. Recommended: the dispatchable resolve, since it keeps lock.sh the only writer.
- Status: open

## duplication

### r4.f12 - r3.f21 grew: COLMAP and Blender added to every list, and their lookups copied into the toolsets

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `scripts/cloud/lock.sh:217-245,247,450,463` at bb8929c (with `images/toolsets/3d/install.sh:41-49`, `images/toolsets/3d/build-colmap.sh:18-24,45`, `images/toolsets/gpu/install.sh`)
- Evidence: #103 added colmap and blender to `release_repo`, `cmd_resolve`, `pinned_value` and `upstream_newest`, and a fifth list, `release_latest`. Blender's index URL and both `sed` patterns are in lock.sh's `blender_latest` and again in 3d/install.sh. `colmap/colmap` and the `/releases/latest` redirect lookup are in lock.sh and in build-colmap.sh. lock.sh:247 still calls `resolve_release` "a tool from a GitHub release". Extends r3.f21.
- Verified: read at bb8929c; if blender.org renames its files, the resolver and the build's `latest` fallback can diverge.
- Recommendation: r3.f21's fix, for four tools: declare each tool's source beside its constraint in the script that reads it, drive lock.sh's lists from one table, and fix the comment.
- Status: open

### r4.f13 - The toolset scripts restate install-toolset.sh's apt, log and die

- Severity: low
- Dimension: duplication
- Kind: fix
- Location: `images/toolsets/3d/install.sh:20-25,33-38`, `images/toolsets/gpu/install.sh:19-20,39-44`, `images/toolsets/3d/build-colmap.sh:12-16,31-32` at bb8929c (with `images/toolsets/install-toolset.sh:40,49-54`)
- Evidence: 3d/install.sh copies `apt()` and `install_apt`'s body, including the `APT_DROP_LISTS` drop, which gpu/install.sh already copied; build-colmap.sh hand-rolls a third apt call; each declares its own `log` and `die`.
- Verified: byte-for-byte copies at the lines above. The missing `NEEDRESTART_SUSPEND` in build-colmap.sh is harmless in a container.
- Recommendation: have install-toolset.sh `export -f apt install_apt log die` before it runs a toolset's install.sh, so the scripts call `install_apt` on their generated list.
- Status: open

### r4.f14 - Each GPU Dockerfile restates NVIDIA_DRIVER_CAPABILITIES

- Severity: info
- Dimension: duplication
- Kind: decision
- Location: `images/cloud/3d-gpu/Dockerfile:32-34`, `images/cloud/linux-gpu/Dockerfile:10-14` at bb8929c (with `specs/image-toolsets.md:351-353`)
- Evidence: both set `ENV NVIDIA_DRIVER_CAPABILITIES=all`, a fact about every GPU image. A third type that forgets it loses EGL, Vulkan and NVENC, and the gpu smoke only reports those.
- Verified: image-toolsets.md documents the per-type rule, and the two copies agree; the per-type gpu RUN layer is the repo's pattern.
- Recommendation: set it once on `base-gpu`, or test that every Dockerfile running `install-toolset.sh gpu` sets it.
- Status: open
