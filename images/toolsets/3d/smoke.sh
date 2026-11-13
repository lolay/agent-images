#!/bin/bash
# smoke: live (needs a GPU)
# The 3d toolset's smoke test (specs/gcp.md, m4): gsplat rasterizes Gaussians on
# the GPU through its compiled extension, and COLMAP extracts SIFT features with
# CUDA. Blender's Cycles is reported, not failed: its first GPU render loads
# kernels for longer than a smoke test should wait.
set -euo pipefail

python3 - <<'PY'
import torch
from gsplat import rasterization
from gsplat.cuda._backend import _C

assert _C is not None, "gsplat has no compiled extension"
dev = torch.device("cuda")
n = 256
means = torch.randn(n, 3, device=dev) * 0.5 + torch.tensor([0.0, 0.0, 4.0], device=dev)
quats = torch.nn.functional.normalize(torch.randn(n, 4, device=dev), dim=-1)
scales = torch.full((n, 3), 0.05, device=dev)
opacities = torch.full((n,), 0.9, device=dev)
colors = torch.rand(n, 3, device=dev)
viewmats = torch.eye(4, device=dev)[None]
Ks = torch.tensor([[[200.0, 0, 64], [0, 200.0, 64], [0, 0, 1]]], device=dev)
image, alpha, _ = rasterization(means, quats, scales, opacities, colors, viewmats, Ks, 128, 128)
torch.cuda.synchronize()
assert alpha.max().item() > 0.5, "gsplat rendered nothing"
print(f"gsplat: {n} Gaussians on {torch.cuda.get_device_name()}")
PY

# Eight images of one textured plane from shifted viewpoints, then CUDA SIFT.
python3 - <<'PY'
import numpy as np
from PIL import Image

rng = np.random.default_rng(0)
texture = (rng.random((600, 800)) * 255).astype(np.uint8)
texture = np.array(Image.fromarray(texture).resize((1600, 1200), Image.BILINEAR))
for i in range(8):
    x, y = 40 * i, 20 * i
    Image.fromarray(texture[y:y + 480, x:x + 640]).save(f"img{i}.png")
PY
mkdir -p images && mv img*.png images/
colmap feature_extractor --database_path db.db --image_path images \
	--FeatureExtraction.use_gpu 1 >colmap.log 2>&1 || {
	tail -n 20 colmap.log
	exit 1
}
if grep -qiE 'Switch from CUDA to OpenGL|no CUDA-capable device|Shader not supported' colmap.log; then
	grep -iE 'CUDA|OpenGL|Shader' colmap.log
	exit 1
fi
features="$(python3 -c 'import sqlite3; print(sqlite3.connect("db.db").execute("select sum(rows) from keypoints").fetchone()[0] or 0)')"
((features > 0)) || {
	echo "colmap extracted no features"
	exit 1
}

cycles="$(timeout 60 blender -b --factory-startup --python-expr '
import bpy
s = bpy.context.scene
s.render.engine = "CYCLES"
p = bpy.context.preferences.addons["cycles"].preferences
p.compute_device_type = "CUDA"
p.get_devices()
for d in p.devices:
    d.use = d.type == "CUDA"
s.cycles.device = "GPU"
s.cycles.samples = 1
s.render.resolution_x = s.render.resolution_y = 64
s.render.filepath = "/tmp/cycles-smoke.png"
bpy.ops.render.render(write_still=True)
print("cycles-smoke:", ",".join(d.name for d in p.devices if d.use) or "CPU")
' 2>/dev/null | sed -n 's/^cycles-smoke: //p' || true)"
echo "3d: gsplat and COLMAP SIFT on the GPU ($features features); Cycles on ${cycles:-nothing}"
