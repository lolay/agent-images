# Test: `linux-gpu` runner

You're in a session on the `linux-gpu` runner type: a Linux container with an NVIDIA
GPU (an RTX PRO 6000, Blackwell, compute capability 12.0, on Cloud Run), thrown away
after the session. The point of this test is proof that work actually runs on that
GPU, through code compiled for it, and not quietly on the CPU or in software
rendering. If a check fails, record it and carry on. Time every step. Work in
`$HOME/runner-test`, and write outputs to
`tests/sessions/results/linux-gpu/<UTC timestamp>/` in the repo.

## 0. Watch the GPU the whole time

Before anything else, start logging in the background and keep it running to the end:

```
nvidia-smi --query-gpu=timestamp,utilization.gpu,memory.used,power.draw,clocks.sm \
  --format=csv -l 1 > gpu.csv &
```

For each step below, record the peak utilization and memory from `gpu.csv` during
that step's time window. A GPU step whose peak utilization stays under 20% fails,
whatever its own output says.

## 1. Facts

The shared facts (see [linux.md](linux.md), step 1), plus: `nvidia-smi` (GPU name,
memory, driver version, CUDA version the driver supports),
`nvidia-smi --query-gpu=compute_cap --format=csv`, `nvcc --version`, and where the
driver libraries come from (`ldconfig -p | grep libcuda`; on Cloud Run they're
mounted under `/usr/local/nvidia/lib64`). Pass if the GPU is an RTX PRO 6000 with
compute capability 12.0 and the CUDA toolkit isn't newer than the driver supports.

## 2. CUDA compiled for this GPU

Write a small CUDA program: vector add plus a tiled matrix multiply in shared memory,
checked against a CPU result.

- Build it with `nvcc -arch=sm_120` and run it. Pass if the results match.
- Show what's inside with `cuobjdump --list-elf` (expect `sm_120`).
- Build it again for an older architecture only, without PTX
  (`-gencode arch=compute_80,code=sm_80`), and run it. It should fail with a "no
  kernel image" error: that's the failure an image gets when a package isn't built
  for Blackwell, and seeing it here proves the check above means something.
- Time the matrix multiply at 8192 by 8192 and report GFLOPS.

## 3. PyTorch on the GPU

In a fresh `uv` virtual environment, install PyTorch's CUDA build that matches the
toolkit (look up the right index URL; don't take the CPU wheel).

- Pass if `torch.cuda.is_available()`, `torch.cuda.get_device_capability()` is
  `(12, 0)`, and `torch.cuda.get_arch_list()` includes `sm_120`.
- Benchmark an 8192 by 8192 matmul in bf16 and fp32 on the GPU (warm up first,
  synchronize, average 20 runs) against fp32 on the CPU, and report TFLOPS and the
  speedup.
- Train a small convolutional network on CIFAR-10 for one epoch on the GPU and
  record the time and final accuracy.

## 4. Graphics on the GPU, not in software

- `eglinfo -B` (or a minimal EGL program): the vendor and renderer must be NVIDIA,
  not Mesa or llvmpipe.
- `vulkaninfo --summary`: the device must be the RTX PRO 6000.
- Render offscreen with OpenGL through EGL (for example `moderngl` with
  `backend="egl"`): a lit, textured, spinning mesh, 300 frames at 1920 by 1080, saved
  as `spin.mp4` and one frame as `frame.png`. Record frames per second and the
  `GL_RENDERER` string. Then force software rendering
  (`__EGL_VENDOR_LIBRARY_FILENAMES` pointing at Mesa, or `LIBGL_ALWAYS_SOFTWARE=1`)
  and render the same 300 frames for comparison.
- If `$DISPLAY` is set (Xvfb), run `vglrun glxinfo -B` and check the renderer is
  NVIDIA, then screenshot the display while the render runs to `display.png`.

## 5. Git and report

Stop the logger. Commit the results directory (report, `gpu.csv`, PNGs, `spin.mp4`)
on the session's branch and push; no pull request. Write `report.md` in the format
from [README.md](README.md), with a GPU column in the check table (peak utilization
and memory per step), and end the session with that table.
