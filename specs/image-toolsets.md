# Image toolsets (proposal)

Status: **proposal**, written 2026-11-05. Changes how every Linux-based image picks
its tools; lands as the first steps of [gcp.md](gcp.md) m2, before `gis` and
`android` are built.

## The problem

`agent-base` installs `images/linux/packages.txt` and `images/linux/Brewfile`, which
are the Android image's lists (the LXD alias `agent-linux` is the Android image). So
every cloud type inherits whatever the Android image needed, and the Android image
inherits whatever the cloud base wanted:

- Every Cloud Run image ships OpenJDK, the emulator's runtime libraries and
  `cpu-checker`, on a platform with no KVM. The cloud `CLAUDE.md` says there's no
  Android SDK; it doesn't mention the JDK that's there by accident.
- The Android image picked up Go, Rust, protobuf, Terraform and Chromium's libraries
  in 367b823 because the cloud base wanted them.
- A change to the Android image's package list rebuilds every Cloud Run type
  (`changed-targets.sh` lists `images/linux/*` under "what every type is built from").
- `gis` and `3d-gpu` are planned as `FROM agent-base`, so they'd get Go, Rust,
  Terraform, Chromium's libraries and the JDK, none of which they need, and a
  Homebrew Python that is wrong for them (below).
- The session tools are defined twice: from Homebrew on the Mac, from Ubuntu's
  archive on Linux. Ubuntu 24.04's `gh` is 2.45 (early 2024), and the Mac has `sops`
  where Linux doesn't.

## The rule for what goes in an image

On Cloud Run a session can't `sudo`, so anything that needs apt has to be in the image
or it doesn't exist. Everything else a session can install itself, in user space,
with Homebrew, uv, rustup or pixi. So an image carries:

1. **What needs root to install** and the type plausibly uses: system libraries,
   headers, X and GL libraries, the CUDA toolkit.
2. **What is slow or huge to get per session:** anything built from source for the
   GPU (COLMAP, gsplat), QGIS and its stack, the Android SDK with a booted AVD
   snapshot.
3. **The toolchains the type's sessions use most days**, to save minutes per session.

Nothing else. "The other image has it" isn't a reason. Everything on this list costs
cold-start time on Cloud Run, where image pull is most of a spawn and there's no
standby.

## Toolsets

A toolset is one named group of tools with one install script, independent of the
base it lands on: the same `node` toolset installs on `ubuntu:24.04`, on NVIDIA's
CUDA image, in an LXD VM, and (Homebrew parts) on macOS. A type is a base OS image
plus an ordered list of toolsets, and nothing is inherited from another type.

```
images/toolsets/
  <name>/packages.txt   apt packages (Linux only), optional
  <name>/Brewfile       Homebrew formulae, optional (Linux and macOS)
  <name>/install.sh     anything else (rustup toolchain, tenv, tflint, the SDK), optional
  install-toolset.sh    installs one toolset: apt list, then Brewfile, then install.sh
```

| Toolset | Contents | Why it's a unit |
| --- | --- | --- |
| `core` | Homebrew itself; apt: build-essential, curl, ca-certificates, file, procps, zip, unzip, mandoc; Brewfile: gh, git, jq, ripgrep, tmux, age, sops, shellcheck, actionlint, nowline, triage | Every session on every platform. The Brewfile is the Mac's session tools too, so they're defined once |
| `native` | apt: pkg-config, cmake, ninja-build, and the headers native builds look for (libssl-dev, zlib1g-dev, libffi-dev, libsqlite3-dev, libpq-dev, libbz2-dev, liblzma-dev, libreadline-dev), sqlite3, postgresql-client | The gap a no-sudo session can't fill: `cargo build` of anything on `openssl-sys`, or a Python package with a C extension and no wheel, fails without these |
| `node` | node, pnpm | |
| `python` | Homebrew's python, uv, ruff, pre-commit | Latest CPython for general work. Not for `gis` or `3d-gpu` (below) |
| `go` | go | |
| `rust` | rustup, stable with clippy and rustfmt | |
| `proto` | protobuf, buf, grpcurl | |
| `terraform` | tenv with the latest Terraform, tflint | |
| `jvm` | OpenJDK 21 | Android, Kotlin and Gradle builds, the Firebase emulators |
| `browser` | Chromium's runtime libraries and fonts (Playwright's list) | apt-only, so a Playwright session on Cloud Run can't add them |
| `android` | Emulator runtime libraries, cpu-checker, JDK 17 beside `jvm`'s 21, the SDK with the newest NDK, Gradle, the AVD and its snapshot, `agent-emulator` | Needs KVM, so VM types only |
| `android-rust` | Rust's Android targets (aarch64, armv7, x86_64) and cargo-ndk | Rust libraries for an Android app, against the `android` toolset's NDK. Needs `rust` and `android` |
| `android-go` | gomobile and gobind | Go libraries for an Android app (`.aar`), against the same NDK. Needs `go` and `android` |
| `gis` | uv, and a pixi environment from conda-forge: GDAL, PDAL with python-pdal, QGIS with PyQGIS, rasterio, geopandas, shapely, pyproj, pystac-client, rioxarray, DuckDB with its spatial extension | Below |
| `gpu` | GLVND's EGL and GLES, Vulkan's loader and vulkaninfo with Mesa's lavapipe, nvtop, VirtualGL (its own release), vendor files pointing EGL and Vulkan at the driver Cloud Run mounts (`/usr/local/nvidia/lib64`), and `agent-cuda-probe`; CUDA's `PATH` and `CUDA_HOME` | The GPU types' last tools layer (m3): the CUDA toolkit comes with the base, the driver from the host, and this routes to it. Last rather than in the base while GPU rendering on Cloud Run is being worked out, so a change to it rebuilds only itself |
| `display` | Xvfb (the entrypoint, or `agent-xvfb.service` on the VM types, starts it at `:99`, 1920x1080), Mesa's software OpenGL (llvmpipe), x11-utils, mesa-utils, ImageMagick for screenshots | A virtual display: headed browsers and desktop apps (QGIS) run and can be screenshotted without a GPU. apt-only, so baked in. GPU types add GPU rendering (VirtualGL, Vulkan) on top in m3 |
| `3d` | Blender (blender.org's build), COLMAP built for compute capability 12.0, ffmpeg, uv, and a uv project with PyTorch's CUDA build, gsplat, Open3D | Below |

## Types

| Type | Base | Toolsets |
| --- | --- | --- |
| `linux` | `ubuntu:24.04` | core, native, node, python, go, rust, proto, terraform, jvm, browser, display |
| `android` (LXD and Compute Engine) | Ubuntu 24.04 VM image | core, native, jvm, display, android, node, go, rust, android-go, android-rust |
| `gis` | `ubuntu:24.04` | core, native, display, node, gis |
| `linux-gpu` | `nvidia/cuda:13.0.x-devel-ubuntu24.04` | everything `linux` has (from `linux`'s own Dockerfile), then gpu |
| `3d-gpu` | the same CUDA image | core, native, display, 3d, gpu |
| `mac` | Cirrus macOS (Tart) | core (Brewfile only), then its own Brewfile: asccli, swiftformat, swiftlint, xcbeautify, xcodegen |

Two patterns fall out of this:

- **`-gpu` is the CPU sibling plus the GPU.** `linux-gpu` is `linux` on a CUDA base
  with a display. A future `gis-gpu` is `gis` the same way. That's what the naming
  in [gcp.md](gcp.md) already promises; toolsets make it literal.
- **`android` is what Android development uses**, on the LXD host and on Compute
  Engine alike, provisioned by the same scripts: JDKs 21 and 17, the SDK with the
  NDK and Gradle, an emulator, Rust with cargo-ndk and Go with gomobile for native
  libraries, Node for React Native, Expo and the Firebase CLI, and the display.
  Not everything `linux` has (below).

### What each type has

Built from the lists above; versions are in the README, newest stable at build time.
"Base" means it comes with the type's base image, not from a toolset. `linux-gpu`
and `3d-gpu` are planned (m3).

| | `linux` | `gis` | `android` | `mac` | `linux-gpu` | `3d-gpu` |
| --- | --- | --- | --- | --- | --- | --- |
| Base | `ubuntu:24.04` | `ubuntu:24.04` | Ubuntu 24.04 VM | Cirrus macOS with Xcode | CUDA 13 devel | CUDA 13 devel |
| sudo in a session | no | no | yes | yes | no | no |
| Session tools (gh, git, jq, ripgrep, tmux, age, sops, shellcheck, actionlint, nowline, triage) | yes | yes | yes | yes | yes | yes |
| Claude Code | yes | yes | yes | yes | yes | yes |
| C toolchain, cmake, ninja, pkg-config | yes | yes | yes | clang and make from Xcode (base) | yes | yes |
| Native headers (OpenSSL, zlib, libffi, SQLite, libpq, and more) | yes | yes | yes | Xcode's SDK (base) | yes | yes |
| Python | Homebrew's newest, with uv, ruff, pre-commit | conda-forge 3.14 (`/opt/gis`), with uv, ruff, pre-commit | 3.12 (base) | Xcode's (base) | as `linux` | pinned for PyTorch (`/opt/3d`), with uv |
| Node, pnpm | yes | yes | yes | no | yes | no |
| Go | yes | no | yes | no | yes | no |
| Rust (rustup stable, clippy, rustfmt) | yes | no | yes | no | yes | no |
| protobuf, buf, grpcurl | yes | no | no | no | yes | no |
| Terraform (tenv), tflint | yes | no | no | no | yes | no |
| JDK | 21 | no | 21 (default), 17 | no | 21 | no |
| Gradle | no (projects' `./gradlew`) | no | yes | no | no | no |
| Android SDK, emulator, AVD | no | no | yes | no | no | no |
| Android NDK | no | no | newest stable | no | no | no |
| Rust Android targets, cargo-ndk | no | no | yes | no | no | no |
| gomobile, gobind | no | no | yes | no | no | no |
| Chromium's libraries (Playwright) | yes | no | no | no | yes | no |
| Display | Xvfb, llvmpipe | Xvfb, llvmpipe | Xvfb, llvmpipe | the VM's (base) | Xvfb, then GPU rendering (m3) | Xvfb, then GPU rendering (m3) |
| GIS: QGIS, GDAL, PDAL, rasterio, geopandas, DuckDB spatial, STAC | no | yes | no | no | no | no |
| CUDA toolkit | no | no | no | no | base | base |
| 3D: Blender, COLMAP, PyTorch, gsplat, Open3D, ffmpeg | no | no | no | no | no | yes |
| Xcode, swiftformat, swiftlint, xcbeautify, xcodegen, asc | no | no | no | yes | no | no |

A type without something can still get it: on `android` and `mac` with sudo or
Homebrew, and on the Cloud Run types with Homebrew, uv, rustup or pixi in user space,
for anything that doesn't need apt.

### `android` is not `linux` plus Android

It was until s8, the LXD image being where every other list came from. By the rule
it carries what Android sessions use most days, and a session there has sudo, so
anything else is an `apt-get` or `brew install` away:

| Toolset | In `android` | Why |
| --- | --- | --- |
| core, native, jvm | yes | The session tools; the headers a native build or `cargo build` needs; the JDK Gradle runs on |
| android | yes | The SDK, emulator and AVD, now with the newest stable NDK (about 2 GB, too big to fetch per session), Gradle for starting a project, and JDK 17 for projects that pin `jvmToolchain(17)` (all three CI images below ship 17). The Android Gradle plugin still fetches the NDK a project pins, if it's another one |
| display | yes | Desktop apps and screenshots of them; the emulator itself stays windowless, and the `android` CLI screenshots the device |
| node | yes | React Native and Expo, the Firebase CLI (its emulators), and the `npx`-run tools agents use, many MCP servers among them. Native Kotlin work doesn't need it |
| rust, android-rust | yes | Rust libraries for Android (UniFFI, or JNI through cargo-ndk) |
| go, android-go | yes | Go libraries for Android through gomobile, which upstream still calls experimental and has never tagged; none of the CI images below ships it |
| fastlane (Ruby) | no | All three CI images below ship it, but it's release automation, and releases run in CI with the store credentials, not here |
| JDK 25 | no | Ubuntu has it (25.0.4), but Android Studio bundles 21, so that's what projects are built and tested with; 17 is there because projects pin it. Add 25 when a project pins it |
| python | no | Ubuntu's `python3` stays, which is enough for SDK and build scripts |
| proto, terraform | no | Not Android work; the protobuf Gradle plugin brings its own protoc |
| browser | no | Playwright's libraries; Android has no headless-browser tests to run |

Not added:

- **Play publishing:** Google has no CLI for the Play Developer API (it publishes
  Java and Python client libraries), and the `android` CLI has no Play commands.
  Projects publish through the Gradle Play Publisher plugin or fastlane's `supply`,
  both per project. Play services, Play Core and Play Integrity are Maven
  dependencies from Google's repository, not SDK packages.
- **Kotlin/Native:** the Kotlin Multiplatform Gradle plugin downloads the toolchain
  matching the project's Kotlin version, so a baked one would rarely be the right
  version. A Linux host builds the `androidNative*` and `linux*` targets, not Apple
  ones (those need the Mac image).
- **Standalone Kotlin, ktlint and bundletool:** a project gets them through Gradle,
  and Homebrew's builds each bring their own JDK. Kotlin DSL build scripts
  (`build.gradle.kts`, `settings.gradle.kts`) need nothing either: Gradle embeds
  the Kotlin compiler for them, and the Kotlin Gradle plugin for app code comes
  from the plugins block.
- **Flutter:** until a repo uses it.

Compared with the common Android CI images (2026-11-06). "Base" means it comes with
Ubuntu's 24.04 image, not from a toolset:

| | `android` (this image) | GitHub `ubuntu-24.04` | CircleCI `cimg/android` | `mingc/android-build-box` |
| --- | --- | --- | --- | --- |
| JDK | 21 (default), 17 | 8, 11, 17 (default), 21, 25 | 8, 17, 21 (default), 25 | 8, 11, 17, 21 |
| SDK platforms | newest stable (37.0) | 34 to 37.2 | 34 to 37.2 | 28 to 35 |
| Build-tools | newest stable (37.0.0) | 34.0.0 to 37.0.0 | 35.0.0 to 37.0.0 | 28.0.1 to 35.0.0 |
| NDK | newest stable (r30) | r27d (default), r29, r30 | `-ndk` variant | r28b |
| CMake (SDK) | on demand (Gradle) | 3.31.5, 4.1.2 | no | yes |
| Emulator and system image | x86_64, booted from a snapshot | no | no | arm only |
| Gradle | 9.8.2 | 9.8.2 | 9.8.2 | not listed |
| Maven, Ant | no | yes | yes | no |
| Kotlin (standalone) | no (Gradle) | 2.4.21 | no | not listed |
| bundletool | no (Gradle) | no | no | yes |
| Node | 26 | 22, 24 | in `-node` | 22 |
| Python | 3.12 (base) | 3.12 (base), more cached | not listed | 3.8 |
| Ruby, fastlane | no | yes | yes | yes |
| Go, gomobile | yes, yes | Go cached, no | no | no |
| Rust, Android targets | yes, with cargo-ndk | Rust, no targets | no | no |
| Flutter | no | no | no | yes |
| gcloud, Firebase CLI | no (no credentials) | gcloud | both | no |
| Virtual display | yes | no | no | no |

### `jvm` in `linux`

The JDK is in `agent-linux` today by accident. It belongs there on purpose: Kotlin
and Gradle work, and the Firebase emulators, need it, and on Cloud Run a session can
only get one through Homebrew's `openjdk`, which is keg-only and needs `JAVA_HOME`
wired up by hand.

### `gis`: conda-forge, not Homebrew or apt

The GIS stack is native libraries with Python bindings that must match them, and
QGIS is the hard part:

- Homebrew has GDAL and PDAL on Linux but no QGIS (QGIS is a macOS cask).
- Ubuntu's or qgis.org's apt packages bind to Ubuntu's `/usr/bin/python3`, and the
  base puts Homebrew's Python ahead of it on PATH, so `python3 -c 'import qgis.core'`
  fails in the image as designed today. They're also older (GDAL 3.8 in 24.04).
- Wheels (rasterio, pyogrio) bundle their own GDAL, which isn't the one `gdalinfo`
  and PDAL use. That's how a session ends up with two GDALs reading the same file
  differently.
- conda-forge carries all of it, current and built against each other, which is why
  it's the geospatial community's default. pixi installs it in user space, so a
  session adds a package with `pixi add` without sudo.

So `gis` has no Homebrew Python. The pixi environment's `bin` is first on PATH, and
uv works against it (`uv venv --python "$GIS_ENV/bin/python" --system-site-packages`)
for project dependencies. The manifest is `images/toolsets/gis/pixi.toml`; each build
runs `pixi update` first, so it tracks the latest like everything else, and the
resolved lock goes into the image manifest.

`node` is there for web-map front ends (MapLibre, deck.gl). Go, Rust, Terraform,
protobuf, the JDK and Chromium's libraries aren't: a GIS session that needs one
installs it.

### `3d-gpu`: a Python pinned for the libraries, not the latest

Same lesson. PyTorch, Open3D and gsplat lag CPython releases (Open3D's wheels have
stopped a version or two behind), and gsplat compiles a CUDA extension against
PyTorch. So the `3d` toolset is a uv project at `/opt/3d` with `requires-python`
set to the newest version all three publish for, PyTorch from its CUDA index, and
gsplat built with `TORCH_CUDA_ARCH_LIST=12.0` at image build so no session compiles
it. COLMAP is built from source in a builder stage (`CMAKE_CUDA_ARCHITECTURES=120`)
and only its install tree is copied in. Blender ships its own Python and its own
Cycles kernels; take blender.org's Linux build, not Ubuntu's.

### `gis` stays CPU; capture and reconstruction go to `3d-gpu`

Decided 2026-11-05, checked against the pipelines these images are meant to serve.
`gis` is CPU work that ends in maps: rasters, vectors, and LiDAR terrain (ground
classification, DTM, DSM, canopy height), which is why PDAL and COPC stay there.
The drone capture and reconstruction side goes to `3d-gpu` in m3, where those
pipelines run:

- exiftool, for capture metadata (GPS, gimbal, vendor XMP): still the standard, since
  Python EXIF libraries miss vendor maker-notes.
- OpenCV (headless) for imagery; kornia for the same on the GPU.
- laspy with lazrs, for NumPy access to LAS, LAZ and COPC alongside PDAL.
- ffmpeg, for frames from capture video.
- Spirula Studio, proposed: a Gaussian splat trainer whose Linux release is one
  Vulkan binary, so nothing has to be compiled for compute capability 12.0 (gsplat
  does). It needs the Vulkan loader, libOpenGL and an NVIDIA ICD manifest when the
  driver ships without one, and Vulkan working on Cloud Run GPUs, which the
  `linux-gpu` test settles. gsplat stays as the scriptable library. Check its license
  before the image redistributes the binary.

Left out, superseded: fiona (pyogrio is geopandas' default), rtree (shapely 2's
STRtree), entwine's EPT (COPC through untwine). Code that still imports them can
`pixi add` them.

The Spirula decision waits on the `linux-gpu` test in m3, which checks Vulkan on
Cloud Run's GPUs (`vulkaninfo` on the RTX PRO 6000). If Vulkan runs there, Spirula
Studio goes in `3d-gpu`'s toolset; if not, it needs the Compute Engine GPU path.

### `gis` on QGIS 4 or QGIS 3

Measured 2026-11-05, solving `gis/pixi.toml` against conda-forge in the image. QGIS
4 is the current line, on Qt 6; 3.44 is the newest 3.x conda-forge solved to. conda-forge's newest
builds can't load everything at once: GDAL's and PDAL's TileDB plugins link
`aws-c-s3` 1.1, while `hdf5` 2.2 and `aws-crt-cpp` hold it below 1.1, and QGIS 4's
MDAL provider links `hdf5` 1.14. No pin fixes all of it on QGIS 4; the one that
loads everything takes the whole stack back to QGIS 3.

| | `gis` as built (newest) | QGIS 3 (`tiledb < 2.30.1`) |
| --- | --- | --- |
| QGIS | 4.2.3 (Qt 6, PyQt6) | 3.44.7 (Qt 5, PyQt5) |
| GDAL | 3.13.3 | 3.12.2 |
| PDAL | 2.10.2 | 2.10.0 |
| HDF5 | 2.2 | 1.14.6 |
| Python | 3.14 | 3.14 |
| Plugins that don't load | 4, dropped at build: GDAL TileDB, PDAL TileDB reader and writer, QGIS MDAL (mesh layers) | none |
| QGIS Python plugins | Only those ported to Qt 6 | The whole QGIS 3 plugin ecosystem |
| Fixes itself | Yes: once conda-forge's builds agree, nothing is dropped | No: the pin stays until someone removes it |

What the dropped plugins cost: TileDB arrays (rare in GIS work, more common in
genomics and some point-cloud archives), and QGIS mesh layers (MDAL: UGRID, 2DM,
NetCDF meshes from hydraulic and climate models). Rasters, vectors, LAS, LAZ, COPC,
EPT and every format GDAL reads without TileDB are unaffected.

`gis` stays on the newest. The case for QGIS 3 is a repo that needs one of those:
a QGIS 3 Python plugin not yet ported to Qt 6, mesh layers, or TileDB.

### Plan: `gis-qgis3`, if a repo needs it

A variant type, named by the convention in [gcp.md](gcp.md) (purpose, then variant,
so it sorts next to `gis`). Not built until a repo needs it.

- s1: `images/toolsets/gis/pixi.qgis3.toml`, the same manifest plus
  `tiledb = "<2.30.1"` (what was measured: it alone solves to QGIS 3.44.7) and
  `qgis = "3.*"` (not measured: it keeps a later solve from drifting to QGIS 4),
  with a comment saying why. `install.sh` takes
  `GIS_MANIFEST` (default `pixi.toml`) so one toolset serves both, and the
  dropped-plugins step stays: it should drop nothing here, and the smoke check
  reports the count.
- s2: `images/cloud/gis-qgis3/Dockerfile`, the `gis` Dockerfile with
  `ARG GIS_MANIFEST=pixi.qgis3.toml` before the gis layer; bake targets
  `gis-qgis3-tools` and `gis-qgis3`; `infra/gcp/gis-qgis3.env` copied from `gis.env`.
- s3: in `check.txt`, a QGIS major check that reads the expected major from the
  manifest, so `gis` asserts 4 and `gis-qgis3` asserts 3.
- s4: build, smoke, and run [tests/sessions/gis.md](../tests/sessions/gis.md) on it;
  in claude.ai, the "Agent: GIS (QGIS 3)" environment and its secret
  `claude-env-gis-qgis3`.
- Retire it when the repo that needed it moves to QGIS 4, or when conda-forge's
  builds agree and the plugin it needed loads on `gis`.

Expect it to drift: each build re-solves, and the `tiledb` pin was measured once. If a later solve breaks it, the smoke check's import test fails
the build rather than shipping it.

### Rust in `gis` or `3d-gpu`

Not in the image. rustup is user space and installs stable in well under a minute on
Cloud Run, so it's the textbook "session installs it" case under the rule. The case
for baking it would be lolay writing Rust in those domains every day (point-cloud
tooling as PyO3 extensions, or Rust splatting like Brush). If that becomes true, add
`rust` to the type's list; it's one line.

## Things this exposes in gcp.md

- **`agent-base-gpu` has to be CUDA devel, not runtime.** The `linux-gpu` test runs
  `nvcc` and `cuobjdump`, and `3d-gpu` builds COLMAP and gsplat for compute
  capability 12.0. The runtime image has neither. Devel is a few GB larger, and both
  GPU types need it.
- **Docker in a session** (Testcontainers, `docker build`) can't work on Cloud Run.
  Repos that need it go to a Compute Engine type, as gcp.md already says for the
  GPU fallback. Worth saying in the cloud `CLAUDE.md`.
- **Unconfirmed on Cloud Run's GPU driver mount:** whether it includes OptiX
  (`libnvoptix`) for Blender and NVENC (`libnvidia-encode`) for ffmpeg. The
  `3d-gpu` test falls back to CUDA and libx264 if not, and records which.

## Build wiring

- **Cloud images:** `agent-base` is Ubuntu, the user and `core`; `base-gpu` is the
  same Dockerfile on CUDA's devel image (`BASE_IMAGE`), the GPU types' parent.
  `linux-gpu` builds `linux`'s Dockerfile on `base-gpu`, then its own adds `gpu`, so
  it can't drift from `linux`. Each type's
  Dockerfile (bake target `<type>-tools`) adds one `RUN install-toolset.sh <name>`
  layer per toolset, slowest-moving first, and the shared `images/cloud/runner`
  stage (target `<type>`) adds the runner files, the session files and Claude Code
  last, so a Claude release rebuilds one layer and never a type's toolsets. Toolsets
  can also carry smoke checks (`check.txt`), a functional smoke test (`smoke.sh`),
  environment (`profile.sh`) and PATH entries (`path.txt`), which
  `install-toolset.sh` records: the smoke runs the image's own checks, and the cloud
  profile puts toolset paths after `~/bin` and ahead of Homebrew. `check.txt` proves
  a tool is there (its version); `smoke.sh` (from
  [gcp.md m4](gcp.md#m4---functional-smoke-and-versions-report)) proves it works: a
  short bash script that builds or runs one small thing with the toolset's main
  tools, offline, in a fresh temp directory, in under 30 s, and ends with a one-line
  summary.
- **LXD and Compute Engine `android`:** `build-image.sh` and the Packer
  `googlecompute` build run `install-toolset.sh` over the same list.
- **macOS:** `install-packages.sh` runs `brew bundle` on `core/Brewfile`, then the
  Mac's own.
- **`changed-targets.sh`** reads each Dockerfile's `install-toolset.sh <name>` lines,
  up the build-parent chain, so a change to a toolset rebuilds only the types that
  install it, and a change to `android` deploys nothing on Cloud Run. The two
  scripts beside the toolsets still rebuild every type.
- **The session `CLAUDE.md`** is written from what's installed: each toolset has a
  `CLAUDE.md` with its bullets, `install-toolset.sh` records each toolset it
  installs (and those bullets) under `/etc/agent-images/toolsets`, and
  `write-claude-md.sh` replaces the template's `<!-- toolsets -->` line with them.
  The cloud and Android templates hold only what's about the machine (sudo or not,
  storage, the emulator), so the list can't claim a tool that isn't there or miss
  one that is.

## Renames

`images/linux` becomes `images/android`, and the LXD alias `agent-linux` becomes
`agent-android`, as gcp.md already suggests. The Linux host hasn't run on hardware
yet, so this is the cheapest it'll ever be. Done in s5, with the names that follow
from it: `agent-android-build` and `-next`, `ANDROID_IMAGE_ALIAS` and `ANDROID_DIR`
in the host scripts and Makefile, and the `android-image-smoke` workflow.
`scripts/linux`, `hooks/linux` and the doctor's `linux` profile keep their names:
they're about the Linux host. A host set up before this needs `IMAGE_REF=agent-android`
in its `.env` (none exists yet).

## Plan

Steps for [gcp.md](gcp.md) m2, ahead of `gis` and `android`:

- s1 (done): `images/toolsets/` and `install-toolset.sh`; split today's
  `packages.txt` and Brewfile into `core`, `node`, `python`, `go`, `rust`, `proto`,
  `terraform`, `jvm`, `browser` and `android`, with no change in what any image
  contains. Until s3 the `android` toolset is only its apt libraries, since
  `agent-linux` still installs them; the SDK stays in `ensure-android-sdk.sh`.
- s2 (done): rebuild `agent-linux` and the LXD image from toolsets; diff the installed
  package lists against the current images (should be identical). The same apt
  packages, formulae, Rust toolchain and Terraform as the pre-toolsets image, and
  5.37 GB against 5.46 GB, once each layer drops apt's lists and Homebrew's caches
  (otherwise they're kept once per layer). The Android image's scripts ran green on
  a hosted runner, with the emulator up from its snapshot in 9 s.
- s3 (done): add `native`; drop `android` from `linux` and move the SDK into the toolset's
  `install.sh`; move session tools to the `core` Brewfile on Linux and the Mac. On
  Linux, git and jq stay in apt: Homebrew's installer needs git, and the hooks call
  jq before Homebrew is on PATH. The Mac gains actionlint and shellcheck from the
  shared list. `browser` now carries Playwright's whole Chromium list: it had
  left out what `android` also installs, so dropping `android` from `linux` dropped
  libnss3 and seven others. Checked by a headless Chromium screenshot in the image.
  The macOS half is untested until a Mac host runs `make build`.
- s4 (done): per-toolset `CLAUDE.md` fragments, and `changed-targets.sh` reading
  toolsets. Both images' session `CLAUDE.md` now list exactly what's installed
  (checked in the cloud build and on the hosted Android run).
- s5 (done): rename `images/linux` to `images/android` and the alias to
  `agent-android` (Renames, above).
- s6 (done): `gis` on toolsets, as in the m2 plan, and the cloud images restructured
  around it (Build wiring, above). The `gis` environment's activation is recorded as
  static exports from `pixi shell-hook --json`: the hook's own script sources
  completion files that fail under the entrypoint's `set -u`. No pins: when
  conda-forge's newest builds disagree (2026-11-05: GDAL's and PDAL's TileDB
  plugins and QGIS's MDAL provider link library versions the rest of the solve
  doesn't have), the build drops the plugins that can't load and lists them in
  `/opt/gis/dropped-plugins.txt`, rather than pin everything back a release (a
  `tiledb` pin cost QGIS 4 for 3.44, GDAL 3.13 for 3.12). The smoke check reports
  the count, and once the builds agree nothing is dropped. The environment also
  carries the cloud-native stack: GeoParquet and Arrow, polars, Zarr, Dask,
  odc-stac, COPC through untwine, tippecanoe and PMTiles, H3, osmium.
- s7 (done): the `display` toolset in `linux` and `gis`: Xvfb at `:99`, which the
  entrypoint starts before the runner (and before the smoke checks), with Mesa's
  software OpenGL, ImageMagick for screenshots, xdotool to size windows (there's no
  window manager) and Open Sans (QGIS downloads it at start otherwise). The session
  tests' display steps (QGIS desktop in `gis`, headed Chromium in `linux`) run
  instead of skipping. Checked in both images: smoke passes the display, OpenGL
  (llvmpipe) and screenshot checks; QGIS 4.2 desktop runs full-screen with a layer
  drawn; headed Chromium runs full-screen with WebGL 1.0. About 60 MB (agent-linux
  went from 5.67 to 5.73 GB).

- s8: `android` gets its own list (above) and the display. The VM types have no
  entrypoint, so `agent-xvfb.service` starts Xvfb at boot and the runner waits for
  it, and the image's profile now sources the toolsets' profile fragments, as the
  cloud one does (`DISPLAY`, and `ANDROID_NDK_HOME` from the `android` toolset).
  `android` gains JDK 17, the NDK, Gradle, `android-rust` and `android-go`, and
  drops `python`, `proto`, `terraform` and `browser`. Checked by
  `android-image-smoke`, which now starts the display, takes a screenshot, and
  builds a Rust library (cargo-ndk) and a Go one (gomobile) for arm64 and x86_64.

`linux-gpu` and `3d-gpu` follow in m3 on the same structure, and both install
`display` (it's in their lists above): the virtual display comes from s7, and m3
adds GPU rendering on top of it (VirtualGL, Vulkan) where Cloud Run's driver allows.
