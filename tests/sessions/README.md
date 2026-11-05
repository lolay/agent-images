# Runner session tests

One test per runner type. Each one exercises what that type is for, the way a real
session would, and ends with a report. Run one after a type is first deployed, after
any change to its image beyond a version bump, and whenever something feels off.

| Type | Test | What it proves |
| --- | --- | --- |
| `linux` | [linux.md](linux.md) | The base: toolchains install and build, network reach, git push, isolation between sessions |
| `android` | [android.md](android.md) | A Compose app builds, unit and instrumented tests pass on the emulator inside the session's VM |
| `gis` | [gis.md](gis.md) | GDAL, PDAL and QGIS on public data: NDVI from Sentinel-2, a canopy height model from USGS 3DEP LiDAR |
| `linux-gpu` | [linux-gpu.md](linux-gpu.md) | The GPU is the one the session gets: CUDA compiled for it, PyTorch on it, OpenGL and Vulkan on it rather than in software |
| `3d-gpu` | [3d-gpu.md](3d-gpu.md) | 3D reconstruction end to end on the GPU: a synthetic scene rendered in Blender, COLMAP on CUDA, dense stereo, Gaussian splatting, all scored against ground truth |
| `mac` | [mac.md](mac.md) | Mac development: a SwiftUI app built and tested on the iOS Simulator, a Swift package on macOS |

## Running one

1. In claude.ai, start a session in the type's environment ("Agent: Linux", and so
   on) on `lolay/agent-images`.
2. Send: `Run the runner test in tests/sessions/<type>.md. Follow it exactly.`
3. Read the summary in the session. The full report and its images are pushed to the
   session's branch under `tests/sessions/results/<type>/<UTC timestamp>/`.

Results stay on the session branch: don't open a pull request for them, and delete
the branch once read. A test that fails a check keeps going and reports every
check; one failure is a finding, not a reason to stop.

## What every test does

- Starts with the same facts: runner type, host, CPU, memory, disk, Claude Code
  version against the published latest, and whether the session can `sudo`.
- Times every step, so runs can be compared over time.
- Uses only public data and public package sources. Nothing needs a secret, and a
  test that seems to need one has found a problem.
- Treats a missing tool as a finding about the image. It installs it to keep going,
  and the report says so.
- Ends with `report.md`: a table of checks (pass, fail, skipped, with the measured
  value), the timings, anything the image was missing, and anything surprising.

## Shared report format

```
# <type> runner test, <UTC timestamp>

| Check | Result | Value |
| --- | --- | --- |
| ... | pass/fail/skipped | measured value or reason |

## Timings
## Missing from the image
## Notes
```
