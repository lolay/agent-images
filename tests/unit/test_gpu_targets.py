"""The GPU images' CUDA targets and driver capabilities live once, on base-gpu
(r4.f9, r4.f14), read as text so they run without Docker.

- docker-bake.hcl's CUDA_CAPABILITIES is the GPU list; base's gpu stage turns
  it into CUDAARCHS and TORCH_CUDA_ARCH_LIST. No toolset script or Dockerfile
  writes a list of its own.
- Every CUDA binary a toolset's check.txt checks for one GPU, it checks for every
  GPU in the list, so an L4 type needs only its env file (specs/gcp.md).
- NVIDIA_DRIVER_CAPABILITIES is set in base's gpu stage and nowhere else.
test_bake.py checks, with buildx, that every type installing gpu builds on it."""

import re
import unittest

from support import REPO

BAKE = REPO / "images/cloud/docker-bake.hcl"
BASE = REPO / "images/cloud/base/Dockerfile"
CLOUD = REPO / "images/cloud"
TOOLSETS = REPO / "images/toolsets"

# A GPU target written out: nvcc's sm_/compute_, or a CMake or PyTorch list
# that starts with a number rather than the variable.
LITERAL_TARGET = re.compile(
    r"\b(?:sm|compute)_\d+|CMAKE_CUDA_ARCHITECTURES=[\"']?\d|TORCH_CUDA_ARCH_LIST=[\"']?\d|CUDAARCHS=[\"']?\d"
)
SM_CHECK = re.compile(r"^(?P<binary>[^:#]+?) sm_(?P<cc>\d+):", re.M)


def capabilities():
    """The compute capabilities docker-bake.hcl declares, as sm_ numbers."""
    block = re.search(r'variable "CUDA_CAPABILITIES" \{\s*default = "([^"]+)"', BAKE.read_text())
    if block is None:
        raise AssertionError("no CUDA_CAPABILITIES variable with a default in docker-bake.hcl")
    return {cap.replace(".", "") for cap in block.group(1).split()}


def literal_targets(paths):
    """(path, line number, text) for each line that writes a GPU target itself."""
    return [
        (path, number, line.strip())
        for path in paths
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if not line.lstrip().startswith("#") and LITERAL_TARGET.search(line)
    ]


def unchecked(check_txt, ccs):
    """{binary: missing sm_ numbers} for each binary check_txt checks for any GPU."""
    checked = {}
    for match in SM_CHECK.finditer(check_txt):
        checked.setdefault(match["binary"], set()).add(match["cc"])
    return {binary: sorted(ccs - found) for binary, found in checked.items() if ccs - found}


def build_files():
    """Every script a cloud build runs and every cloud Dockerfile."""
    return sorted(TOOLSETS.rglob("*.sh")) + sorted(CLOUD.glob("*/Dockerfile"))


class CudaTargetsTests(unittest.TestCase):
    def test_the_list_is_the_l4_and_blackwell(self):
        # Also keeps the tests below from passing on an empty list.
        self.assertEqual(capabilities(), {"89", "120"})

    def test_no_build_writes_its_own_list(self):
        self.assertEqual([f"{path.relative_to(REPO)}:{number}: {line}"
                          for path, number, line in literal_targets(build_files())], [])

    def test_a_written_list_is_found(self):
        # The checker itself, on each form the three builds used before r4.f9.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "install.sh"
            script.write_text(
                "# sm_120 in a comment is fine\n"
                '-DCMAKE_CUDA_ARCHITECTURES="89;120" \\\n'
                'TORCH_CUDA_ARCH_LIST="8.9;12.0+PTX" uv pip install .\n'
                "-gencode arch=compute_89,code=sm_89\n"
                '-DCMAKE_CUDA_ARCHITECTURES="$CUDAARCHS"\n'
            )
            self.assertEqual([number for _, number, _ in literal_targets([script])], [2, 3, 4])

    def test_base_gpu_sets_both_variables_from_bake(self):
        gpu_stage = BASE.read_text().split("FROM base AS gpu", 1)[1]
        for name in ("CUDAARCHS", "TORCH_CUDA_ARCH_LIST"):
            with self.subTest(name=name):
                self.assertRegex(gpu_stage, rf"(?m)^ARG {name}$")
                self.assertRegex(gpu_stage, rf"\b{name}=\$\{{{name}\}}")
        self.assertIn("cmake_cuda_archs(split(\" \", CUDA_CAPABILITIES))", BAKE.read_text())
        self.assertIn("torch_cuda_arch_list(split(\" \", CUDA_CAPABILITIES))", BAKE.read_text())

    def test_every_checked_binary_is_checked_for_every_gpu(self):
        ccs = capabilities()
        problems, binaries = {}, set()
        for check in sorted(TOOLSETS.glob("*/check.txt")):
            text = check.read_text()
            binaries |= {match["binary"] for match in SM_CHECK.finditer(text)}
            if missing := unchecked(text, ccs):
                problems[str(check.relative_to(REPO))] = missing
        self.assertEqual(problems, {})
        # An empty set would pass vacuously: the three CUDA builds are checked.
        self.assertEqual(binaries, {"colmap", "gsplat", "cuda-probe"})

    def test_a_missing_gpu_check_is_found(self):
        text = "colmap sm_120: cuobjdump x | grep sm_120\ngsplat sm_89: a\ngsplat sm_120: b\n"
        self.assertEqual(unchecked(text, {"89", "120"}), {"colmap": ["89"]})


class DriverCapabilitiesTests(unittest.TestCase):
    def test_set_once_in_base_gpu(self):
        setters = [
            f"{path.relative_to(REPO)}:{number}"
            for path in sorted(CLOUD.glob("*/Dockerfile"))
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if re.search(r"\bNVIDIA_DRIVER_CAPABILITIES=", line) and not line.lstrip().startswith("#")
        ]
        self.assertEqual(len(setters), 1, setters)
        base = BASE.read_text()
        stage = base.index("FROM base AS gpu")
        self.assertRegex(base[stage:], r"\bNVIDIA_DRIVER_CAPABILITIES=all\b")


if __name__ == "__main__":
    unittest.main()
