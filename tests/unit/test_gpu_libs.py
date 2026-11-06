"""images/toolsets/gpu/agent-gpu-libs.sh reports which driver libraries are mounted."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO

SCRIPT = REPO / "images/toolsets/gpu/agent-gpu-libs.sh"


def report(lib_dir, *libs):
    for lib in libs:
        (Path(lib_dir) / lib).touch()
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "AGENT_NVIDIA_LIB": str(lib_dir)},
        timeout=30,
    )
    return result


class GpuLibsTests(unittest.TestCase):
    def test_compute_only_mount_reports_graphics_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = report(
                tmp,
                "libcuda.so.1",
                "libcuda.so.580.95.05",
                "libnvidia-ml.so.1",
                "libnvidia-ptxjitcompiler.so.1",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        line = result.stdout.strip()
        self.assertTrue(line.startswith("driver 580.95.05; mounted: compute; missing: egl ("), line)
        for group in ("egl", "glx", "vulkan", "nvenc", "nvdec", "optix"):
            self.assertIn(f"{group} (", line)

    def test_full_mount_reports_nothing_missing(self):
        libs = [
            "libcuda.so.580.95.05",
            "libnvidia-ml.so.1",
            "libnvidia-ptxjitcompiler.so.1",
            "libEGL_nvidia.so.0",
            "libnvidia-eglcore.so.580.95.05",
            "libGLX_nvidia.so.0",
            "libnvidia-glcore.so.580.95.05",
            "libnvidia-glsi.so.580.95.05",
            "libnvidia-glvkspirv.so.580.95.05",
            "libnvidia-encode.so.1",
            "libnvcuvid.so.1",
            "libnvoptix.so.1",
            "libnvidia-rtcore.so.580.95.05",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = report(tmp, *libs)
        self.assertEqual(
            result.stdout.strip(),
            "driver 580.95.05; mounted: compute egl glx vulkan nvenc nvdec optix; missing: none",
        )

    def test_no_driver_directory_still_passes(self):
        result = report("/nonexistent-nvidia-lib")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "no driver mounted at /nonexistent-nvidia-lib")


if __name__ == "__main__":
    unittest.main()
