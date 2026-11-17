- 3D reconstruction, built for this GPU: COLMAP (`colmap`, CUDA SIFT, matching
  and dense stereo; no GUI), Blender (`blender -b`, blender.org's build, Cycles
  on the GPU: OptiX or CUDA), and `/opt/3d`, a locked uv environment first on
  PATH (`python3`): PyTorch's CUDA build, gsplat with its CUDA extension
  compiled, Open3D, OpenCV, kornia, laspy, trimesh. A project environment can't
  see `/opt/3d`'s PyTorch; you own `/opt/3d`, so add packages to it with
  `uv pip install --python /opt/3d/.venv <package>`, which reuses its PyTorch.
- COLMAP 4 spells the GPU flags `--FeatureExtraction.use_gpu` and
  `--FeatureMatching.use_gpu`; "Switch from CUDA to OpenGL" in its log means it
  fell back. `exiftool` reads capture metadata (GPS, gimbal, XMP).
