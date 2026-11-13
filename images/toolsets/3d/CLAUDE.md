- 3D reconstruction, built for this GPU: COLMAP (`colmap`, CUDA SIFT, matching
  and dense stereo; no GUI), Blender (`blender -b`, blender.org's build, Cycles
  on the GPU: OptiX or CUDA), and `/opt/3d`, a locked uv environment first on
  PATH (`python3`): PyTorch's CUDA build, gsplat with its CUDA extension
  compiled, Open3D, OpenCV, kornia, laspy, trimesh. Add packages to your own
  project with uv rather than into `/opt/3d`.
- COLMAP 4 spells the GPU flags `--FeatureExtraction.use_gpu` and
  `--FeatureMatching.use_gpu`; "Switch from CUDA to OpenGL" in its log means it
  fell back. `exiftool` reads capture metadata (GPS, gimbal, XMP).
