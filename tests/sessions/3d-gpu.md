# Test: `3d-gpu` runner

You're in a session on the `3d-gpu` runner type: the GPU runner (an RTX PRO 6000,
Blackwell, compute capability 12.0) plus 3D reconstruction tools: Blender, COLMAP
built with CUDA, and a Gaussian splatting library. Run a full photogrammetry pipeline
on the GPU and score it against ground truth. The scene is synthetic, rendered by you
in Blender, so the true geometry and camera poses are known and nothing needs
downloading. If a check fails, record it and carry on. Time every step. Work in
`$AGENT_SCRATCH/runner-test` (`$HOME/runner-test` if that isn't set), and write outputs to
`tests/sessions/results/3d-gpu/<UTC timestamp>/` in the repo.

## 0. Watch the GPU the whole time

As in [linux-gpu.md](linux-gpu.md), step 0: log `nvidia-smi` to `gpu.csv` every
second from start to end, and record peak utilization and memory per step. Every
step marked **GPU** fails if its peak utilization stays under 20%.

## 1. Facts and builds

The shared facts and GPU facts (see [linux-gpu.md](linux-gpu.md), step 1), plus:
`blender --version`, COLMAP's version and whether it reports CUDA support
(`colmap -h`), and the versions of PyTorch and the splatting library (`gsplat`, or
`nerfstudio` if that's what's installed).

Check that the GPU code in each was built for this GPU: run `cuobjdump --list-elf`
on COLMAP's binary or its CUDA library and on the splatting library's compiled
extension. Pass if each lists `sm_120`, or carries PTX that the driver can compile
for it. A library without either is the failure this runner exists to prevent:
record which one and stop that step rather than letting it fall back to the CPU.

## 2. Scene and capture (GPU)

With a Python script run by `blender -b -P`, build a scene about 30 m by 10 m:

- a ground plane with a high-detail procedural texture (noise and voronoi mixed,
  with displacement), plus a coarse voronoi and noise layer, so it has features at
  every range from 4 m to 15 m;
- a row of seven trees: trunks as tapered cylinders, canopies as several displaced
  icospheres with leafy procedural textures, each tree different in size and color;
- five rocks or crates with distinct textures, between and around the trees;
- sun and sky lighting.

Place 120 cameras like a field capture:

- 72 on the ground: two passes along each side of the row, at 1.5 m and 3 m height,
  18 cameras each, about 70% overlap. The 1.5 m passes look at the trees; the 3 m
  passes look down at the ground in front of them, so the ground ties both sides to
  the aerial set.
- 48 aerial and oblique: a grid of nadir and oblique shots from 15 m, some of the
  obliques steeper, from about 6.5 m out from the row.

A first run with every ground camera looking at the trees and no coarse ground layer
split into three models (the aerial set and each side), so keep both. Render 1920 by
1080 with Cycles on the GPU (OptiX if available, else CUDA), 64 samples, denoised.
Keep a scene mask for each render (the depth pass works: the sky is at infinity), so
the scoring below leaves the sky out. Export:

- `images/` with the renders, and `masks/`;
- `cameras_gt.json`: each camera's intrinsics and pose;
- `scene_gt.ply`: the scene's geometry, triangulated, in the same world frame.

Render one frame on the CPU too, and record GPU and CPU seconds per frame.

## 3. Sparse reconstruction (GPU)

With COLMAP, using the GPU for both features and matching:

- `feature_extractor` with GPU SIFT (the flag is `--FeatureExtraction.use_gpu 1` on
  COLMAP 4, `--SiftExtraction.use_gpu 1` on 3.x; check `-h`), single camera, pinhole
  model;
- `exhaustive_matcher` with GPU matching;
- `mapper` (or COLMAP's global mapper if this version has one, and note which).

Fail the step if any log contains "Switch from CUDA to OpenGL", "no CUDA-capable
device" or "Shader not supported": those mean it fell back. Run feature extraction
on 10 images with the GPU off and record the speedup.

Then score it: align the reconstruction to the ground-truth camera centers (a
similarity transform, `colmap model_aligner` or Umeyama in Python) and report
registered images out of 120, mean reprojection error, camera position RMSE in
meters, and mean rotation error in degrees. Pass if at least 110 images register and
the position RMSE is under 5 cm.

## 4. Dense reconstruction (GPU)

`image_undistorter`, then `patch_match_stereo` (CUDA only) with
`--PatchMatchStereo.max_image_size 960`, then `stereo_fusion` to `fused.ply`. Run
patch match in `tmux`: at full resolution it took 57 minutes, longer than a
background command's time limit, and a lower resolution is enough to check the
runner. Bring it into the ground-truth frame with the transform from step 3 and
compare it with points sampled from `scene_gt.ply` (Open3D is fine): report accuracy
(mean distance from reconstructed points to the truth), completeness, and the point
count. Completeness is the share of truth points within 5 cm of the reconstruction,
counting only truth points some capture camera can see (ray-cast from the camera
centers); report it over all truth points too. Pass if accuracy is under 3 cm and
completeness over 80%. (The canopies overlap, so about a third of the truth surface
is hidden from every camera; over all truth, the first run scored 58.5% with the
visible part at 89.9%.)

## 5. Gaussian splatting (GPU)

Train a Gaussian splatting model on the COLMAP result with the installed library,
holding out every eighth image, for 7,000 steps. gsplat's wheel doesn't ship its
example trainer, so write a small one on `gsplat.rasterization` and its
`DefaultStrategy`. Report training time, steps per second, peak GPU memory, the
number of Gaussians, and PSNR, SSIM and LPIPS on the held-out images.

Score the scene, not the sky: no capture camera constrains the sky, so before scoring
copy the ground truth's sky pixels (from the mask) into the splat render, here and in
step 7. Metric packages (`lpips`, `torchmetrics`) aren't in `/opt/3d`, which
sessions can't write to; install them beside it with `uv pip install --target pylib lpips
torchmetrics` and run with `PYTHONPATH=pylib`. A new virtual environment made with
`--system-site-packages` doesn't see `/opt/3d`'s packages. Render one held-out
view next to its ground-truth image as `splat_vs_gt.png`, and a novel view from a
camera that wasn't in the capture as `splat_novel.png`. Pass if held-out PSNR is
above 25 dB.

## 6. Back in Blender (GPU)

Mesh the fused point cloud (Poisson in Open3D), import it into Blender with the
ground-truth scene, and render one image with the two side by side, the
reconstruction in a single flat color, as `mesh_vs_gt.png`.

## 7. Flythrough video (GPU)

Make one camera path through the scene: 12 seconds at 30 frames per second, starting
high over the end of the row, descending along one side at tree height, crossing the
row between two trees, and climbing out over the other end. It should pass through
places no capture camera was, so it shows what the reconstruction really learned.

- **Ground truth:** render the path in Blender with Cycles on the GPU (OptiX if
  available), 1920 by 1080, 64 samples, denoised.
- **Reconstruction:** render the same path from the Gaussian splatting model on the
  GPU. Record frames per second.
- **Video:** encode `flythrough.mp4`: the two side by side (1920 by 540 per frame),
  labelled "Ground truth (Cycles)" and "Gaussian splats", with the frame's PSNR in a
  corner. Encode on the GPU with `h264_nvenc` if ffmpeg has it and the driver exposes
  NVENC; otherwise `libx264`, and record which. Keep it under 20 MB.
- **Preview:** a 480-pixel-wide, 10 frames per second `flythrough.gif` of the same
  video, under 8 MB, so the report shows it inline on GitHub.

Pass if both renders ran on the GPU (peak utilization above 50% in each; Cycles
averages lower because each frame also denoises and writes a PNG), the video plays
(`ffprobe` shows 360 frames), and the mean PSNR over the path is reported.

## 8. Git and report

Stop the logger. Commit the results directory on the session's branch and push; no
pull request. Include the report, `gpu.csv`, `cameras_gt.json`, the PNGs, three
sample renders, `flythrough.mp4` and `flythrough.gif`. Copy the large outputs to
`$AGENT_OUTPUTS`: the image set, the COLMAP sparse and dense models, `fused.ply`, the
Poisson mesh, the splat model, and the full-resolution flythrough frames. List them
in the report with their `gs://` paths and sizes, and the total. Write `report.md` in the
format from [README.md](README.md), with a GPU column (peak utilization and memory
per step) and a metrics table (registration, pose error, accuracy, completeness,
PSNR, SSIM, LPIPS, splat rendering frames per second). Embed `flythrough.gif` at the
top of the report, then `splat_vs_gt.png` and `mesh_vs_gt.png`. End the session with
both tables, links to `flythrough.mp4` and the report on the branch, and the
`gs://` path of the outputs.

The pass thresholds are a first guess for this scene; if a run fails one but the
outputs look right, say so, and the threshold gets tuned rather than ignored.
