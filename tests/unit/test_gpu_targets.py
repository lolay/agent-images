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
    """The compute capabilities docker-bake.hcl declares, as sm_ numbers. The one
    place a test spells the list out is test_the_list_is_the_l4_and_blackwell."""
    block = re.search(r'variable "CUDA_CAPABILITIES" \{\s*default = "([^"]+)"', BAKE.read_text())
    if block is None:
        raise AssertionError("no CUDA_CAPABILITIES variable with a default in docker-bake.hcl")
    return {cap.replace(".", "") for cap in dotted_capabilities(block.group(1))}


def dotted_capabilities(value):
    """The capabilities in a CUDA_CAPABILITIES value, in order: what bake's
    regexall finds, so stray spaces make no empty entry."""
    return re.findall(r"[0-9]+\.[0-9]+", value)


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
    """Every script and build config a cloud build reads, and every cloud
    Dockerfile: a toolset's shell and Python scripts and its pyproject.toml."""
    toolset_files = [p for pattern in ("*.sh", "*.py", "*.toml") for p in TOOLSETS.rglob(pattern)]
    return sorted(toolset_files) + sorted(CLOUD.glob("*/Dockerfile"))


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
        bake = BAKE.read_text()
        self.assertRegex(bake, r"CUDAARCHS\s+= cmake_cuda_archs\([^\n]*\bCUDA_CAPABILITIES\)\)")
        self.assertRegex(bake, r"TORCH_CUDA_ARCH_LIST\s+= torch_cuda_arch_list\([^\n]*\bCUDA_CAPABILITIES\)\)")

    def test_both_forms_put_ptx_on_the_newest_capability(self):
        """CUDAARCHS and TORCH_CUDA_ARCH_LIST each give the last entry of their
        list the PTX, so each reads the capabilities through oldest_first, which
        sorts them: "12.0 8.9" would otherwise put PTX on compute_89 and lose it
        for Blackwell (r5.f3). Read as text, so it runs without Docker;
        test_bake.py hands the real bake an unsorted list and reads the result."""
        bake = BAKE.read_text()
        self.assertRegex(bake, r'function "oldest_first" \{[^}]*\bvalues\(')
        functions = re.findall(r'function "(\w+)" \{\s*params = \[caps\]\s*result = ([^\n]+)', bake)
        users = {name: result for name, result in functions if name != "oldest_first"}
        self.assertEqual(sorted(users), ["cmake_cuda_archs", "torch_cuda_arch_list"])
        for name, result in users.items():
            with self.subTest(name=name):
                self.assertIn("oldest_first(caps)", result)
                # No other use of the list as given, which would order by position
                # again (its length is the one other thing a form needs).
                rest = result.replace("oldest_first(caps)", "").replace("length(caps)", "")
                self.assertNotRegex(rest, r"\bcaps\b")

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

    def test_the_probes_ptx_is_checked_for_the_newest_gpu(self):
        # r5.f6: the probe carries PTX for the last entry of the list (base-gpu's
        # cmake_cuda_archs leaves it without -real), which sm_ lines can't see.
        # A line in gpu/check.txt that lists the probe's PTX for that capability.
        newest = dotted_capabilities(
            re.search(r'variable "CUDA_CAPABILITIES" \{\s*default = "([^"]+)"', BAKE.read_text()).group(1))[-1]
        text = (TOOLSETS / "gpu/check.txt").read_text()
        line = re.search(r"^cuda-probe ptx %s:(.*)$" % newest.replace(".", ""), text, re.M)
        self.assertIsNotNone(line, f"no `cuda-probe ptx {newest.replace('.', '')}:` line in gpu/check.txt")
        self.assertIn("cuobjdump --list-ptx", line[1])
        self.assertIn(f"_{newest.replace('.', '')}'", line[1])
        # It isn't an sm_ line, so the per-GPU rule above doesn't count it as one.
        self.assertNotIn("ptx", {match["binary"] for match in SM_CHECK.finditer(text)})

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
