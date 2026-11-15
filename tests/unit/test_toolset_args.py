"""The lock's toolchain versions reach the build only through `ARG` lines: without
one, BuildKit ignores bake's `--set` and the toolset's install.sh falls back to its
default (`stable`, `latest`), and the layer's cache key stops following the lock
(r3.f14).

Nothing here is a list of names. The variables are the ones `lock.sh bake-args`
prints for the repo's locks; a script reads one if it has `${NAME:-default}`; a
Dockerfile stage runs a toolset's install.sh with `install-toolset.sh <name>`, or a
toolset's own script directly with `bash /tmp/toolsets/<name>/<script>.sh` (a
builder stage, as 3d-gpu's COLMAP: r4.f1)."""

import re
import subprocess
import unittest

from support import REPO

CLOUD = REPO / "images/cloud"
TOOLSETS = REPO / "images/toolsets"
RUNS_TOOLSET = re.compile(r"install-toolset\.sh\s+(\w+)")
RUNS_SCRIPT = re.compile(r"bash\s+/tmp/toolsets/(\w+)/([\w.-]+\.sh)")


def locked_variables():
    """Every NAME in a `*.args.NAME=value` line that bake-args prints for a type."""
    names = set()
    for lock in sorted(CLOUD.glob("*/versions.lock")):
        out = subprocess.run(
            ["bash", str(REPO / "scripts/cloud/lock.sh"), "bake-args", lock.parent.name],
            cwd=REPO, capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        names.update(re.findall(r"^\*\.args\.(\w+)=", out, re.M))
    return names


def fed_by(toolset, variables, script="install.sh"):
    """The locked variables a toolset's script reads."""
    path = TOOLSETS / toolset / script  # optional: not every toolset has an install.sh
    text = path.read_text() if path.exists() else ""
    return {name for name in variables if re.search(r"\$\{%s:-" % name, text)}


def scripts_run(line):
    """(toolset, script) for each toolset script a Dockerfile line runs."""
    runs = [(match.group(1), "install.sh") for match in RUNS_TOOLSET.finditer(line)]
    return runs + [(match.group(1), match.group(2)) for match in RUNS_SCRIPT.finditer(line)]


def violations(dockerfile, variables):
    """`<toolset> needs ARG <NAME> earlier in its stage` for each missing ARG."""
    missing, declared = [], set()
    for line in dockerfile.read_text().splitlines():
        if line.startswith("FROM "):
            declared = set()
        if match := re.match(r"ARG (\w+)", line):
            declared.add(match.group(1))
        for toolset, script in scripts_run(line):
            what = toolset if script == "install.sh" else f"{toolset}/{script}"
            missing += [f"{what} needs ARG {name} before it"
                        for name in sorted(fed_by(toolset, variables, script) - declared)]
    return missing


class ToolsetArgsTests(unittest.TestCase):
    def test_every_locked_version_a_toolset_reads_has_its_arg(self):
        variables = locked_variables()
        problems = {}
        for dockerfile in sorted(CLOUD.glob("*/Dockerfile")):
            if found := violations(dockerfile, variables):
                problems[str(dockerfile.relative_to(REPO))] = found
        self.assertEqual(problems, {})

    def test_the_derivation_finds_the_toolchains(self):
        # An empty map would pass the test above vacuously.
        variables = locked_variables()
        self.assertEqual(
            {toolset: fed_by(toolset, variables) for toolset in ("rust", "terraform", "gpu", "3d")},
            {"rust": {"RUST_TOOLCHAIN"}, "terraform": {"TERRAFORM_VERSION", "TFLINT_VERSION"},
             "gpu": {"VIRTUALGL_VERSION"}, "3d": {"BLENDER_VERSION"}},
        )
        self.assertEqual(fed_by("3d", variables, "build-colmap.sh"), {"COLMAP_VERSION"})
        dockerfile = (CLOUD / "3d-gpu/Dockerfile").read_text()
        self.assertIn(("3d", "build-colmap.sh"), [run for line in dockerfile.splitlines() for run in scripts_run(line)])

    def test_a_missing_arg_is_reported(self):
        # The checker itself: a stage that runs rust without its ARG fails.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text("FROM scratch AS a\nARG RUST_TOOLCHAIN\nFROM scratch AS b\n"
                                  "RUN bash /tmp/toolsets/install-toolset.sh rust\n")
            self.assertEqual(violations(dockerfile, {"RUST_TOOLCHAIN"}),
                             ["rust needs ARG RUST_TOOLCHAIN before it"])

    def test_a_builder_stage_missing_its_arg_is_reported(self):
        # A stage that runs a toolset's own script, not install-toolset.sh (r4.f1).
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text("FROM scratch AS colmap\n"
                                  "RUN --mount=type=bind,source=images/toolsets/3d,target=/tmp/toolsets/3d \\\n"
                                  "\tbash /tmp/toolsets/3d/build-colmap.sh\n")
            self.assertEqual(violations(dockerfile, {"COLMAP_VERSION"}),
                             ["3d/build-colmap.sh needs ARG COLMAP_VERSION before it"])


if __name__ == "__main__":
    unittest.main()
