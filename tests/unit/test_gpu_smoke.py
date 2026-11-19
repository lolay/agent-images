"""images/toolsets/gpu/smoke.sh against fake nvidia-smi, agent-cuda-probe, agent-gpu-libs, eglinfo, vulkaninfo and ffmpeg."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable

SCRIPT = REPO / "images/toolsets/gpu/smoke.sh"
LIBS_LINE = "driver 580.126.20; mounted: compute, EGL, GLX, NVDEC; missing: OptiX (libnvoptix.so.1)"


class GpuSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        # Each fake accepts only the arguments smoke.sh passes.
        write_executable(
            self.bin / "nvidia-smi",
            '[[ "$*" == "--query-gpu=name,driver_version,compute_cap --format=csv,noheader" ]]'
            ' || { echo "nvidia-smi: unknown arguments: $*" >&2; exit 2; }\n'
            "echo 'NVIDIA L4, 580.126.20, 8.9'\n",
        )
        write_executable(
            self.bin / "agent-cuda-probe",
            '[[ $# -eq 0 ]] || { echo "agent-cuda-probe: takes no arguments" >&2; exit 2; }\necho "cuda: kernel ok"\n',
        )
        write_executable(
            self.bin / "eglinfo",
            '[[ "$*" == "-B -p surfaceless" ]] || { echo "eglinfo: unknown arguments: $*" >&2; exit 2; }\n'
            "echo 'OpenGL core profile renderer: NVIDIA L4/PCIe/SSE2'\n",
        )
        write_executable(
            self.bin / "vulkaninfo",
            '[[ "$*" == "--summary" ]] || { echo "vulkaninfo: unknown arguments: $*" >&2; exit 2; }\n'
            "echo '	deviceName         = NVIDIA L4'\n",
        )
        write_executable(
            self.bin / "ffmpeg",
            '[[ "$*" == "-hide_banner -loglevel error -f lavfi -i testsrc2=size=640x360:rate=30'
            ' -frames:v 30 -c:v h264_nvenc -f null -" ]]'
            ' || { echo "ffmpeg: unknown arguments: $*" >&2; exit 2; }\n',
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def smoke(self, libs_body):
        write_executable(
            self.bin / "agent-gpu-libs",
            '[[ $# -eq 0 ]] || { echo "agent-gpu-libs: takes no arguments" >&2; exit 2; }\n' + libs_body,
        )
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=base_env(self.tmp, self.bin),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def last_line(self, result):
        return result.stdout.rstrip("\n").split("\n")[-1]

    def test_last_line_carries_the_mounted_and_missing_groups(self):
        result = self.smoke(f"echo '{LIBS_LINE}'")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.last_line(result),
            "gpu: NVIDIA L4 ran a CUDA kernel; EGL on NVIDIA L4/PCIe/SSE2; Vulkan on NVIDIA L4;"
            f" NVENC encodes: yes; {LIBS_LINE}",
        )

    def test_a_failing_agent_gpu_libs_is_reported_not_fatal(self):
        result = self.smoke("echo 'boom' >&2; exit 3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.last_line(result).endswith("NVENC encodes: yes; agent-gpu-libs failed (exit 3)"))
        self.assertIn("boom", result.stdout)

    def test_a_silent_agent_gpu_libs_is_reported(self):
        result = self.smoke("true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.last_line(result).endswith("NVENC encodes: yes; agent-gpu-libs printed nothing"))


if __name__ == "__main__":
    unittest.main()
