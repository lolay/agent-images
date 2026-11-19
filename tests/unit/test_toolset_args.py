"""The lock's toolchain versions reach the build only through `ARG` lines: without
one, BuildKit ignores bake's `--set` and the toolset's install.sh falls back to its
default (`stable`, `latest`), and the layer's cache key stops following the lock
(r3.f14).

Nothing here is a list of names. The variables are the ones `lock.sh bake-args`
prints for the repo's locks; a script reads one if it has `${NAME:-default}`; a
Dockerfile stage runs a toolset's install.sh with `install-toolset.sh <name>`, or a
toolset's own script directly with `bash /tmp/toolsets/<name>/<script>.sh` (a
builder stage, as 3d-gpu's COLMAP: r4.f1).

Which script declares a locked tool is lock.sh's to say, not this file's: its
release tools come from any `images/toolsets/<toolset>/*.sh`, so `lock.sh releases
<type>` (its second column) gives the file set, and a script no Dockerfile runs
directly is read by the toolset's install.sh stage, as 3d's blender.sh is (r5.f4)."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO
from test_bake import run_types

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


def declaring_scripts(types, repo=REPO):
    """{(toolset, script)} that lock.sh says declare a release tool in one of the
    types: the second column of `lock.sh releases <type>`, a path
    images/toolsets/<toolset>/<script>.sh."""
    found = set()
    for type_ in types:
        out = subprocess.run(
            ["bash", str(REPO / "scripts/cloud/lock.sh"), "releases", type_],
            cwd=repo, capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        for line in out.splitlines():
            path = Path(line.split()[1])
            found.add((path.parent.name, path.name))
    return found


def direct_runs(dockerfiles):
    """{(toolset, script)} that some Dockerfile runs on its own, not through the
    toolset's install.sh: a builder stage's script, with a stage of its own."""
    return {
        (toolset, script)
        for dockerfile in dockerfiles
        for line in dockerfile.read_text().splitlines()
        for toolset, script in scripts_run(line)
        if script != "install.sh"
    }


def fed_by(toolset, variables, script="install.sh", declaring=frozenset(), direct=frozenset(), root=TOOLSETS):
    """The locked variables a toolset's script reads. For install.sh that is its
    own and those of every other script lock.sh reads in the toolset (declaring),
    except the ones a Dockerfile runs in a stage of their own (direct) and
    install.sh doesn't call: a helper install.sh runs reads its variable in
    install.sh's stage, even when a builder stage runs it too."""
    def read(name):
        path = root / toolset / name  # optional: not every toolset has an install.sh
        return path.read_text() if path.exists() else ""

    scripts = {script}
    if script == "install.sh":
        install_code = "".join(line for line in read(script).splitlines(True) if not line.lstrip().startswith("#"))
        scripts |= {name for tool, name in declaring
                    if tool == toolset and ((tool, name) not in direct or name in install_code)}
    text = "".join(read(name) for name in scripts)
    return {name for name in variables if re.search(r"\$\{%s:-" % name, text)}


def scripts_run(line):
    """(toolset, script) for each toolset script a Dockerfile line runs."""
    runs = [(match.group(1), "install.sh") for match in RUNS_TOOLSET.finditer(line)]
    return runs + [(match.group(1), match.group(2)) for match in RUNS_SCRIPT.finditer(line)]


def violations(dockerfile, variables, declaring=frozenset(), direct=frozenset(), root=TOOLSETS):
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
                        for name in sorted(fed_by(toolset, variables, script, declaring, direct, root) - declared)]
    return missing


def repo_view():
    """What the checks read from the repo: the locked variables, the scripts lock.sh
    reads release tools from, and the scripts Dockerfiles run directly."""
    dockerfiles = sorted(CLOUD.glob("*/Dockerfile"))
    return locked_variables(), declaring_scripts(run_types()), direct_runs(dockerfiles)


class ToolsetArgsTests(unittest.TestCase):
    def test_every_locked_version_a_toolset_reads_has_its_arg(self):
        variables, declaring, direct = repo_view()
        problems = {}
        for dockerfile in sorted(CLOUD.glob("*/Dockerfile")):
            if found := violations(dockerfile, variables, declaring, direct):
                problems[str(dockerfile.relative_to(REPO))] = found
        self.assertEqual(problems, {})

    def test_the_derivation_finds_the_toolchains(self):
        # An empty map would pass the test above vacuously.
        variables, declaring, direct = repo_view()
        self.assertEqual(
            {toolset: fed_by(toolset, variables, declaring=declaring, direct=direct)
             for toolset in ("rust", "terraform", "gpu", "3d")},
            {"rust": {"RUST_TOOLCHAIN"}, "terraform": {"TERRAFORM_VERSION", "TFLINT_VERSION"},
             "gpu": {"VIRTUALGL_VERSION"}, "3d": {"BLENDER_VERSION"}},
        )
        self.assertEqual(fed_by("3d", variables, "build-colmap.sh"), {"COLMAP_VERSION"})
        self.assertIn(("3d", "build-colmap.sh"), direct)
        self.assertIn(("3d", "install.sh"), declaring)  # blender: the declaring file lock.sh names
        dockerfile = (CLOUD / "3d-gpu/Dockerfile").read_text()
        self.assertIn(("3d", "build-colmap.sh"), [run for line in dockerfile.splitlines() for run in scripts_run(line)])

    def test_a_missing_arg_is_reported(self):
        # The checker itself: a stage that runs rust without its ARG fails.
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text("FROM scratch AS a\nARG RUST_TOOLCHAIN\nFROM scratch AS b\n"
                                  "RUN bash /tmp/toolsets/install-toolset.sh rust\n")
            self.assertEqual(violations(dockerfile, {"RUST_TOOLCHAIN"}),
                             ["rust needs ARG RUST_TOOLCHAIN before it"])

    def test_a_builder_stage_missing_its_arg_is_reported(self):
        # A stage that runs a toolset's own script, not install-toolset.sh (r4.f1).
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text("FROM scratch AS colmap\n"
                                  "RUN --mount=type=bind,source=images/toolsets/3d,target=/tmp/toolsets/3d \\\n"
                                  "\tbash /tmp/toolsets/3d/build-colmap.sh\n")
            self.assertEqual(violations(dockerfile, {"COLMAP_VERSION"}),
                             ["3d/build-colmap.sh needs ARG COLMAP_VERSION before it"])


class HelperScriptTests(unittest.TestCase):
    """r5.f4: a locked version read by a helper script install.sh runs. The tree
    is a type and a toolset of its own, so lock.sh reads the declaration the way
    it does in the repo."""

    DECLARATION = ('xtool="${XTOOL_VERSION:-latest}"\n'
                   "readonly XTOOL_RELEASES=https://github.com/acme/xtool/releases\n")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "images/cloud/base").mkdir(parents=True)
        (self.tmp / "images/cloud/base/Dockerfile").write_text("FROM mirror.gcr.io/library/ubuntu:24.04\n")
        (self.tmp / "images/cloud/demo").mkdir()
        self.dockerfile = self.tmp / "images/cloud/demo/Dockerfile"
        self.toolset = self.tmp / "images/toolsets/widgets"
        self.toolset.mkdir(parents=True)
        self.root = self.tmp / "images/toolsets"

    def violations(self, dockerfile_text, declare_in, install_text='bash "$here/fetch-xtool.sh"\n'):
        """The ARGs missing with the declaration in <declare_in>, a script of the toolset."""
        self.dockerfile.write_text(dockerfile_text)
        (self.toolset / "install.sh").write_text("#!/bin/bash\n" + install_text)
        (self.toolset / "fetch-xtool.sh").write_text("#!/bin/bash\n")
        (self.toolset / declare_in).write_text("#!/bin/bash\n" + self.DECLARATION)
        declaring = declaring_scripts(["demo"], repo=self.tmp)
        direct = direct_runs([self.dockerfile])
        return declaring, violations(self.dockerfile, {"XTOOL_VERSION"}, declaring, direct, self.root)

    def test_a_helper_scripts_declaration_is_read_in_installs_stage(self):
        declaring, missing = self.violations(
            "FROM agent-base\nRUN bash /tmp/toolsets/install-toolset.sh widgets\n", "fetch-xtool.sh")
        self.assertEqual(declaring, {("widgets", "fetch-xtool.sh")})
        self.assertEqual(missing, ["widgets needs ARG XTOOL_VERSION before it"])

    def test_the_same_declaration_in_install_sh_is_found(self):
        declaring, missing = self.violations(
            "FROM agent-base\nRUN bash /tmp/toolsets/install-toolset.sh widgets\n", "install.sh")
        self.assertEqual(declaring, {("widgets", "install.sh")})
        self.assertEqual(missing, ["widgets needs ARG XTOOL_VERSION before it"])

    def test_the_arg_before_the_stage_satisfies_it(self):
        _, missing = self.violations(
            "FROM agent-base\nARG XTOOL_VERSION\nRUN bash /tmp/toolsets/install-toolset.sh widgets\n",
            "fetch-xtool.sh")
        self.assertEqual(missing, [])

    def test_a_script_run_in_its_own_stage_and_by_install_sh_is_read_by_both(self):
        # Copilot's review of #142: the builder stage has its ARG, the final
        # stage doesn't, and install.sh calls the same script.
        dockerfile = ("FROM agent-base AS build\nARG XTOOL_VERSION\n"
                      "RUN bash /tmp/toolsets/widgets/fetch-xtool.sh\n"
                      "FROM agent-base\nRUN bash /tmp/toolsets/install-toolset.sh widgets\n")
        _, missing = self.violations(dockerfile, "fetch-xtool.sh")
        self.assertEqual(missing, ["widgets needs ARG XTOOL_VERSION before it"])

    def test_install_sh_only_mentioning_the_script_in_a_comment_doesnt_count(self):
        # build-colmap.sh's own case: 3d/install.sh may name it in a comment.
        dockerfile = ("FROM agent-base AS build\nARG XTOOL_VERSION\n"
                      "RUN bash /tmp/toolsets/widgets/fetch-xtool.sh\n"
                      "FROM agent-base\nRUN bash /tmp/toolsets/install-toolset.sh widgets\n")
        _, missing = self.violations(dockerfile, "fetch-xtool.sh",
                                     install_text="# fetch-xtool.sh builds it elsewhere\n")
        self.assertEqual(missing, [])

    def test_a_script_run_in_its_own_stage_is_not_read_by_installs(self):
        # 3d's build-colmap.sh: the install stage doesn't need its ARG, the builder does.
        dockerfile = ("FROM agent-base AS build\nRUN bash /tmp/toolsets/widgets/fetch-xtool.sh\n"
                      "FROM agent-base\nRUN bash /tmp/toolsets/install-toolset.sh widgets\n")
        _, missing = self.violations(dockerfile, "fetch-xtool.sh", install_text="log hello\n")
        self.assertEqual(missing, ["widgets/fetch-xtool.sh needs ARG XTOOL_VERSION before it"])


if __name__ == "__main__":
    unittest.main()
