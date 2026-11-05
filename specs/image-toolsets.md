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
| `python` | Homebrew's python, uv | Latest CPython for general work. Not for `gis` or `3d-gpu` (below) |
| `go` | go | |
| `rust` | rustup, stable with clippy and rustfmt | |
| `proto` | protobuf, buf, grpcurl | |
| `terraform` | tenv with the latest Terraform, tflint | |
| `jvm` | OpenJDK 21 | Android, Kotlin and Gradle builds, the Firebase emulators |
| `browser` | Chromium's runtime libraries and fonts (Playwright's list) | apt-only, so a Playwright session on Cloud Run can't add them |
| `android` | Emulator runtime libraries, cpu-checker, the SDK, the AVD and its snapshot (`ensure-android-sdk.sh`), `agent-emulator` | Needs KVM, so VM types only |
| `gis` | uv, and a pixi environment from conda-forge: GDAL, PDAL with python-pdal, QGIS with PyQGIS, rasterio, geopandas, shapely, pyproj, pystac-client, rioxarray, DuckDB with its spatial extension | Below |
| `cuda` | Nothing installed: a marker that the base is NVIDIA's CUDA devel image | Below |
| `display` | Xvfb, VirtualGL, mesa-utils, vulkan-tools, libegl1, x11-utils | GPU types' virtual head |
| `3d` | Blender (blender.org's build), COLMAP built for compute capability 12.0, ffmpeg, uv, and a uv project with PyTorch's CUDA build, gsplat, Open3D | Below |

## Types

| Type | Base | Toolsets |
| --- | --- | --- |
| `linux` | `ubuntu:24.04` | core, native, node, python, go, rust, proto, terraform, jvm, browser |
| `android` (LXD and Compute Engine) | Ubuntu 24.04 VM image | everything `linux` has, then android |
| `gis` | `ubuntu:24.04` | core, native, node, gis |
| `linux-gpu` | `nvidia/cuda:13.0.x-devel-ubuntu24.04` | everything `linux` has, then cuda, display |
| `3d-gpu` | the same CUDA image | core, native, display, 3d |
| `mac` | Cirrus macOS (Tart) | core (Brewfile only), then its own Brewfile: asccli, swiftformat, swiftlint, xcbeautify, xcodegen |

Two patterns fall out of this:

- **`-gpu` is the CPU sibling plus the GPU.** `linux-gpu` is `linux` on a CUDA base
  with a display. A future `gis-gpu` is `gis` the same way. That's what the naming
  in [gcp.md](gcp.md) already promises; toolsets make it literal.
- **`android` is `linux` plus Android**, on the LXD host and on Compute Engine alike,
  provisioned by the same scripts. A session on `android` can do anything one on
  `linux` can (and has sudo besides). This is what the LXD image is today; it just
  stops being the thing every other image is built from.

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

- **Cloud images:** each type's Dockerfile is its base, then one `RUN
  install-toolset <name>` per toolset (one layer each, slowest-moving first), then the
  shared runner files and Claude Code, as now. There's no `agent-base` build parent;
  the shared part is the `core` layer, which bake builds once per base image.
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
- s6: `gis` on toolsets, as in the m2 plan.

`linux-gpu` and `3d-gpu` follow in m3 on the same structure.
