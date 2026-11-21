"""images/cloud/docker-bake.hcl builds every type infra/gcp declares.

The types are the BACKEND=run env files (scripts/cloud/changed-targets.sh --all);
the build graph is the bake file. Nothing else ties the two together, so a new
env file without its targets would pass CI and fail the deploy on main (r1.f2).
"""

import json
import re
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, git_env

BAKE_FILE = "images/cloud/docker-bake.hcl"
CHANGED_TARGETS = REPO / "scripts/cloud/changed-targets.sh"

HAVE_BAKE = shutil.which("docker") is not None and subprocess.run(
    ["docker", "buildx", "version"], capture_output=True
).returncode == 0


def run_types():
    """The Cloud Run types, as the deploy and the pull request check plan them."""
    plan = subprocess.run(
        ["bash", str(CHANGED_TARGETS), "--all"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout
    return [entry["type"] for entry in json.loads(plan)["types"]]


def bake_print(*targets):
    result = subprocess.run(
        ["docker", "buildx", "bake", "-f", BAKE_FILE, "--print", *targets],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"bake --print {' '.join(targets)} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)["target"]


class BakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_BAKE:
            # The pull request check runs these: there, a missing buildx fails.
            if os.environ.get("GITHUB_ACTIONS") == "true":
                raise AssertionError("needs docker buildx, and this CI runner has none")
            raise unittest.SkipTest("needs docker buildx")

    def test_there_are_types(self):
        # An empty list would make the checks below pass vacuously.
        self.assertIn("linux", run_types())

    def test_every_type_has_its_targets(self):
        for type_ in run_types():
            with self.subTest(type=type_):
                targets = bake_print(type_)
                # The deploy pushes <type> and caches <type>-tools by those names
                # (cloud-deploy.yml); <type> is the runner stage on <type>-tools.
                self.assertIn(f"{type_}-tools", targets)
                image = targets[type_]
                self.assertEqual(image["dockerfile"], "images/cloud/runner/Dockerfile")
                self.assertEqual(image["contexts"]["agent-tools"], f"target:{type_}-tools")
                self.assertEqual(image["tags"], [f"agent-{type_}:local"])
                self.assertEqual(image["target"], "runner")
                self.assertEqual(image["args"]["AGENT_TYPE"], type_)

    def test_every_type_has_its_versions_report(self):
        for type_ in run_types():
            with self.subTest(type=type_):
                # The workflows read out/versions/<type>/versions.md after the bake.
                report = bake_print(f"versions-{type_}")[f"versions-{type_}"]
                self.assertEqual(report["target"], "versions")
                self.assertEqual(report["args"]["AGENT_TYPE"], type_)
                self.assertEqual(report["contexts"]["agent-tools"], f"target:{type_}-tools")
                self.assertEqual(report["output"], [{"type": "local", "dest": f"out/versions/{type_}"}])
                self.assertNotIn("tags", report)

    def test_push_and_load_keep_the_reports_local_output(self):
        # The deploy pushes and the pull request check loads in the same bake call
        # as the report; neither may turn the report into an image.
        for flag in ("--push", "--load"):
            with self.subTest(flag=flag):
                report = bake_print(flag, "linux", "versions-linux")["versions-linux"]
                self.assertEqual(report["output"], [{"type": "local", "dest": "out/versions/linux"}])

    def test_cloud_build_builds_each_runner_types_report_with_the_commit(self):
        """make cloud-build reproduces the pull request check's build (r2.f14): each
        target through scripts/cloud/bake.sh, a runner type from its lock with its
        versions report naming COMMIT, the orchestrator alone. The real make and
        bake.sh run, with a fake docker that records each bake call."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name in run_types():
            (tmp / "locks" / name).mkdir(parents=True)
            (tmp / "locks" / name / "versions.lock").write_text(
                "apt-snapshot - 20261101T000000Z\nbase - ubuntu@sha256:aaa\nclaude - 2.1.319\n"
            )
        (tmp / "bin").mkdir()
        docker = tmp / "bin" / "docker"
        docker.write_text(
            "#!/bin/bash\n"
            'if [[ "$1 $2" == "buildx bake" ]]; then printf \'%s %s\\n\' "$COMMIT" "${*: -2}" >>"$FAKE_CALLS"; exit 0; fi\n'
            'exec "$REAL_DOCKER" "$@"\n'
        )
        docker.chmod(0o755)
        env = {**os.environ, "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "LOCK_DIR": str(tmp / "locks"),
               "FAKE_CALLS": str(tmp / "calls"), "REAL_DOCKER": shutil.which("docker")}

        def built(*args):
            calls = tmp / "calls"
            calls.unlink(missing_ok=True)
            subprocess.run(["make", "-s", "cloud-build", *args], cwd=REPO, env=env, check=True,
                           capture_output=True, text=True)
            return calls.read_text().splitlines()

        self.assertEqual(built("TYPE=linux", "COMMIT=abc123"), ["abc123 linux versions-linux"])
        self.assertEqual([line.split(" ", 1)[1] for line in built("TYPE=orchestrator")],
                         ["--load orchestrator"], "not a runner: no report")
        every = [line.split(" ", 1)[1] for line in built("TYPE=all")]
        for name in run_types():
            self.assertIn(f"{name} versions-{name}", every)
        self.assertIn("--load orchestrator", every)
        self.assertEqual(len(every), len(run_types()) + 1)

    def test_every_gpu_type_builds_on_base_gpu(self):
        """A type that installs the gpu toolset builds on base's gpu stage, which
        sets the driver capabilities and the CUDA targets (r4.f9, r4.f14); a type
        that doesn't, doesn't. Read from the bake graph and the Dockerfiles, so a
        new type is covered the day it lands."""
        gpu_types = []
        for type_ in run_types():
            toolsets = subprocess.run(
                ["bash", "-c", 'source scripts/cloud/lib.sh && cloud_toolsets "$1"', "-", type_],
                cwd=REPO, check=True, capture_output=True, text=True,
            ).stdout.split()
            stages = [target.get("target") for target in bake_print(type_).values()
                      if target["dockerfile"] == "images/cloud/base/Dockerfile"]
            with self.subTest(type=type_):
                self.assertEqual(stages, ["gpu"] if "gpu" in toolsets else ["base"])
            if "gpu" in toolsets:
                gpu_types.append(type_)
        self.assertIn("linux-gpu", gpu_types)

    def test_base_gpu_hands_the_cuda_targets_to_cmake_and_pytorch(self):
        # SASS for each GPU and PTX for the newest, in each tool's spelling, from
        # the list itself (test_gpu_targets.py pins the list), including a value
        # with stray spaces, which must not make an empty entry, and values out of
        # order: the newest is the highest capability, not the last one written
        # (r5.f3), and "12.0" sorts after "8.9" though not as text.
        from test_gpu_targets import BAKE as HCL, dotted_capabilities
        default = re.search(r'variable "CUDA_CAPABILITIES" \{\s*default = "([^"]+)"', HCL.read_text())[1]
        for value in (default, "  7.5  8.9 12.0 ", "12.0 8.9", " 8.9 12.0 7.5", "8.9"):
            caps = sorted(dotted_capabilities(value), key=lambda cap: tuple(map(int, cap.split("."))))
            numbers = [cap.replace(".", "") for cap in caps]
            expected = (";".join([f"{n}-real" for n in numbers[:-1]] + numbers[-1:]), ";".join(caps) + "+PTX")
            with self.subTest(value=value):
                result = subprocess.run(
                    ["docker", "buildx", "bake", "-f", BAKE_FILE, "--print", "base-gpu"],
                    cwd=REPO, capture_output=True, text=True, timeout=60,
                    env={**os.environ, "CUDA_CAPABILITIES": value},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads(result.stdout)["target"]["base-gpu"]["args"]
                self.assertEqual((args["CUDAARCHS"], args["TORCH_CUDA_ARCH_LIST"]), expected)

    def test_a_gpu_listed_twice_fails_the_bake(self):
        # oldest_first keys a map by capability; a repeat must stop the build, not
        # collapse into one entry the author didn't ask for.
        result = subprocess.run(
            ["docker", "buildx", "bake", "-f", BAKE_FILE, "--print", "base-gpu"],
            cwd=REPO, capture_output=True, text=True, timeout=60,
            env={**os.environ, "CUDA_CAPABILITIES": "8.9 12.0 8.9"},
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Duplicate object key", result.stderr)

    def test_a_malformed_gpu_list_fails_the_bake(self):
        # An entry the derivation can't read must stop the build, not drop out
        # of it: "12" alone would build every GPU image without Blackwell.
        for value in ("8.9 12", "89 120", "8.9,12.0", " "):
            with self.subTest(value=value):
                result = subprocess.run(
                    ["docker", "buildx", "bake", "-f", BAKE_FILE, "--print", "base-gpu"],
                    cwd=REPO, capture_output=True, text=True, timeout=60,
                    env={**os.environ, "CUDA_CAPABILITIES": value},
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("CUDA_CAPABILITIES is compute capabilities", result.stderr)

    def test_the_orchestrator_has_its_target(self):
        self.assertEqual(bake_print("orchestrator")["orchestrator"]["tags"], ["agent-orchestrator:local"])

    def test_the_orchestrator_takes_the_newest_claude_not_the_runner_types(self):
        """A CLAUDE_VERSION pin holds the runner types; the orchestrator always
        takes the newest (r3.f22), and the deploy's ORCHESTRATOR_CLAUDE_VERSION
        sets it."""
        def claude(env):
            result = subprocess.run(
                ["docker", "buildx", "bake", "-f", BAKE_FILE, "--print", "orchestrator", "linux"],
                cwd=REPO, capture_output=True, text=True, timeout=60,
                env={key: value for key, value in {**os.environ, **env}.items()
                     if key not in ("CLAUDE_VERSION", "ORCHESTRATOR_CLAUDE_VERSION") or key in env},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            targets = json.loads(result.stdout)["target"]
            return targets["orchestrator"]["args"]["CLAUDE_VERSION"], targets["linux"]["args"]["CLAUDE_VERSION"]

        self.assertEqual(claude({}), ("latest", "latest"))
        self.assertEqual(claude({"CLAUDE_VERSION": "2.1.317"}), ("latest", "2.1.317"))
        self.assertEqual(claude({"ORCHESTRATOR_CLAUDE_VERSION": "2.1.320"}), ("2.1.320", "latest"))

    def test_changed_targets_follows_the_bake_graph(self):
        """A change to any Dockerfile directory in a type's bake graph rebuilds that type.

        changed-targets reads the graph from the Dockerfiles' FROM lines, not the
        bake file, so this checks the two agree (r1.f32: a type FROM agent-base-gpu
        never reached base).
        """
        needed = {}  # directory under images/cloud -> the types whose graph uses it
        for type_ in run_types():
            for target in bake_print(type_).values():
                directory = Path(target["dockerfile"]).parent.name
                needed.setdefault(directory, set()).add(type_)

        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)

        def git(*args):
            return subprocess.run(
                ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=git_env()
            ).stdout.strip()

        git("init", "-q", "-b", "main")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "test")
        for name in ("images/cloud", "images/toolsets", "infra/gcp"):
            shutil.copytree(REPO / name, repo / name)
        git("add", "-A")
        git("commit", "-q", "-m", "tree")

        for directory, types in sorted(needed.items()):
            with self.subTest(directory=directory):
                base = git("rev-parse", "HEAD")
                dockerfile = repo / "images/cloud" / directory / "Dockerfile"
                dockerfile.write_text(dockerfile.read_text() + "# changed\n")
                git("commit", "-q", "-am", f"change {directory}")
                plan = subprocess.run(
                    ["bash", str(CHANGED_TARGETS), base, git("rev-parse", "HEAD")],
                    cwd=repo,
                    env=git_env(),
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                built = {entry["type"] for entry in json.loads(plan)["types"] if entry["build"]}
                self.assertLessEqual(types, built)


class AptBuildConfTests(unittest.TestCase):
    """images/cloud/base/apt-build.conf reaches every update a cloud build
    runs, and no further, read as text (a build would need the archive down)."""

    CONF = "images/cloud/base/apt-build.conf"
    INSTALLED = "/etc/apt/apt.conf.d/40agent-build"

    def text(self, path):
        return (REPO / path).read_text()

    def test_the_conf_fails_an_update_on_any_failed_download(self):
        self.assertIn('\nAPT::Update::Error-Mode "any";\n', self.text(self.CONF))

    def test_the_base_installs_it_before_its_first_update(self):
        # Through apt-prepare.sh, which reads it from beside itself; that the
        # install comes before every update is test_apt_prepare's (its apt-get
        # fails an update without the file).
        base = self.text("images/cloud/base/Dockerfile")
        self.assertIn(f"source={self.CONF},target=/tmp/apt-build.conf", base)
        prepare = self.text("images/cloud/base/apt-prepare.sh")
        self.assertIn('readonly conf_dir="$root/etc/apt/apt.conf.d"', prepare)
        installed = prepare.index('step install -D -m 644 "$here/apt-build.conf" "$conf_dir/40agent-build"')
        self.assertLess(installed, prepare.index("step apt_update"))
        self.assertEqual(self.INSTALLED, "/etc/apt/apt.conf.d/40agent-build")

    def test_the_orchestrator_passes_it_for_its_update(self):
        # Its update is apt_update's (install-toolset.sh, mounted to source), run
        # with APT_CONFIG exported; test_apt_update checks it is the only update.
        orchestrator = self.text("images/cloud/orchestrator/Dockerfile")
        self.assertIn(f"source={self.CONF},target=/tmp/apt-build.conf", orchestrator)
        run = orchestrator.split("export APT_CONFIG=/tmp/apt-build.conf", 1)[1].split("apt-get install", 1)[0]
        self.assertIn("bash -c 'source /tmp/install-toolset.sh && apt_update'", run)
        self.assertNotIn("apt-get update", orchestrator)

    def test_the_runner_stage_removes_it(self):
        self.assertIn(self.INSTALLED, self.text("images/cloud/runner/Dockerfile").split("rm -f", 1)[1].splitlines()[0])


if __name__ == "__main__":
    unittest.main()
