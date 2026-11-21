"""scripts/cloud/lib.sh and hooks/gcp/lib.sh's per-type settings: one reader each
for what a type is built from and how it's configured (AGENTS.md, "One source
per fact")."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO


def run(cwd: Path, script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{REPO}/scripts/cloud/lib.sh"; AGENT_GCP_ENV_DIR=infra/gcp; {script}'],
        cwd=cwd, capture_output=True, text=True,
    )


class CloudToolsetsTests(unittest.TestCase):
    def test_linux_gpu_installs_base_then_linux_then_gpu(self):
        """linux-gpu is linux's Dockerfile on base-gpu, then gpu: the real chain."""
        result = run(REPO, "cloud_toolsets linux-gpu")
        self.assertEqual(result.returncode, 0, result.stderr)
        toolsets = result.stdout.split()
        self.assertEqual(toolsets[0], "core")
        self.assertEqual(toolsets[-1], "gpu")
        self.assertLess(toolsets.index("native"), toolsets.index("go"))
        self.assertEqual(len(toolsets), len(set(toolsets)))

    def test_order_is_root_first_and_file_order_within(self):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        files = {
            "images/cloud/base/Dockerfile": "FROM ubuntu\nRUN bash /t/install-toolset.sh core\n",
            "images/cloud/mid/Dockerfile": (
                "FROM agent-base\nRUN bash /t/install-toolset.sh b\nRUN bash /t/install-toolset.sh a\n"
            ),
            "images/cloud/top/Dockerfile": "FROM agent-mid\nRUN bash /t/install-toolset.sh z\n",
        }
        for name, text in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        result = run(repo, "cloud_toolsets top")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["core", "b", "a", "z"])

    def test_base_gpu_maps_to_base(self):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        for name, text in {
            "images/cloud/base/Dockerfile": "FROM ubuntu\nRUN bash /t/install-toolset.sh core\n",
            "images/cloud/g/Dockerfile": "FROM agent-base-gpu\nRUN bash /t/install-toolset.sh gpu\n",
        }.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        result = run(repo, "cloud_chain g; cloud_toolsets g")
        self.assertEqual(result.stdout.split(), ["g", "base", "core", "gpu"])


class TypeSettingTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        env = self.repo / "infra/gcp"
        env.mkdir(parents=True)
        (env / "project.env").write_text("PROJECT_ID=p\n")
        (env / "plain.env").write_text("CPU=4\n")
        (env / "weekly.env").write_text("REBUILD=weekly\nBACKEND=compute\nDEPLOY=false\n")
        (env / "empty.env").write_text("REBUILD=\n")

    def value(self, script):
        result = run(self.repo, script)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_defaults_when_unset_or_empty(self):
        self.assertEqual(self.value("gcp_rebuild_class plain"), "nightly")
        self.assertEqual(self.value("gcp_rebuild_class empty"), "nightly")
        self.assertEqual(self.value("gcp_backend plain"), "run")

    def test_values_from_the_env_file(self):
        self.assertEqual(self.value("gcp_rebuild_class weekly"), "weekly")
        self.assertEqual(self.value("gcp_backend weekly"), "compute")

    def test_deploys(self):
        self.assertEqual(self.value("gcp_deploys plain && echo yes || echo no").strip(), "yes")
        self.assertEqual(self.value("gcp_deploys weekly && echo yes || echo no").strip(), "no")

    def test_missing_env_file_prints_nothing(self):
        self.assertEqual(self.value("gcp_type_setting gone REBUILD nightly"), "")

    def test_settings_do_not_leak_into_the_caller(self):
        self.assertEqual(self.value('gcp_rebuild_class weekly >/dev/null; echo "${REBUILD:-unset}"').strip(), "unset")


class CheckTypeTests(unittest.TestCase):
    """gcp_check_type: the one reader of the DEPLOY=false-on-compute rule."""

    def check(self, env_dir: Path, type_: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", f'set -euo pipefail; source "{REPO}/hooks/gcp/lib.sh"; gcp_check_type {type_}'],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "AGENT_GCP_ENV_DIR": str(env_dir)},
        )

    def fixture(self, **files: str) -> Path:
        env_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, env_dir)
        (env_dir / "project.env").write_text("PROJECT_ID=p\n")
        for name, text in files.items():
            (env_dir / f"{name}.env").write_text(text)
        return env_dir

    def test_refuses_deploy_false_on_a_compute_type_with_one_message(self):
        env_dir = self.fixture(vm="BACKEND=compute\nDEPLOY=false\n")
        result = self.check(env_dir, "vm")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count("\n"), 1)
        self.assertIn("vm: DEPLOY=false is for Cloud Run types only", result.stderr)
        self.assertIn("remove it from infra/gcp/vm.env", result.stderr)

    def test_accepts_the_other_combinations(self):
        env_dir = self.fixture(
            run_false="DEPLOY=false\n", run_true="DEPLOY=true\n", vm="BACKEND=compute\n", vm_true="BACKEND=compute\nDEPLOY=true\n"
        )
        for type_ in ("run_false", "run_true", "vm", "vm_true"):
            with self.subTest(type=type_):
                result = self.check(env_dir, type_)
                self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_refuses_an_image_family_in_any_env_file(self):
        # The family is always agent-<type> (gcp_image_family): a key that named
        # it would do nothing, so it's refused, not ignored (r1.f20).
        env_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, env_dir)
        shutil.copytree(REPO / "infra/gcp", env_dir, dirs_exist_ok=True)
        types = sorted(path.stem for path in env_dir.glob("*.env") if path.stem != "project")
        for type_ in types:
            with (env_dir / f"{type_}.env").open("a") as file:
                file.write(f"IMAGE_FAMILY=agent-{type_}-renamed\n")
        refused = self.refused_types(env_dir)
        self.assertEqual(sorted(refused), types)
        for type_, message in refused.items():
            self.assertEqual(
                message,
                f"{type_}: IMAGE_FAMILY is not a setting: a type's image family is always agent-{type_}; "
                f"remove it from infra/gcp/{type_}.env\n",
            )

    def test_image_family_is_agent_type(self):
        result = subprocess.run(
            ["bash", "-c", f'source "{REPO}/hooks/gcp/lib.sh"; gcp_image_family android'],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.stdout, "agent-android\n")

    def refused_types(self, env_dir: Path) -> dict[str, str]:
        """Every type in env_dir that gcp_check_type refuses, with its message."""
        listed = subprocess.run(
            ["bash", "-c", f'source "{REPO}/hooks/gcp/lib.sh"; gcp_types'],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "AGENT_GCP_ENV_DIR": str(env_dir)},
        )
        types = listed.stdout.split()
        self.assertTrue(types, "no runner types found")
        results = {type_: self.check(env_dir, type_) for type_ in types}
        return {type_: result.stderr for type_, result in results.items() if result.returncode != 0}

    def test_the_real_env_files_pass(self):
        """A bad infra/gcp/*.env fails its pull request here, not on the host (r3.f2)."""
        self.assertEqual(self.refused_types(REPO / "infra/gcp"), {})

    def test_the_real_env_files_check_goes_red_on_a_bad_compute_file(self):
        env_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, env_dir)
        shutil.copytree(REPO / "infra/gcp", env_dir, dirs_exist_ok=True)
        with (env_dir / "android.env").open("a") as file:
            file.write("DEPLOY=false\n")
        refused = self.refused_types(env_dir)
        self.assertEqual(list(refused), ["android"])
        self.assertIn("DEPLOY=false is for Cloud Run types only", refused["android"])


def parse_github_env(text: str) -> dict[str, str]:
    """$GITHUB_ENV as the runner reads it: NAME=value, or NAME<<DELIMITER, the
    lines, then DELIMITER."""
    values, lines = {}, iter(text.splitlines())
    for line in lines:
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            name, delimiter = line.split("<<", 1)
            body = []
            for next_line in lines:
                if next_line == delimiter:
                    break
                body.append(next_line)
            else:
                raise AssertionError(f"{name}: no closing {delimiter}")
            values[name] = "\n".join(body)
        else:
            name, value = line.split("=", 1)
            values[name] = value
    return values


class GithubEnvTests(unittest.TestCase):
    """scripts/cloud/github-env.sh: the workflows' one reader of project.env (r1.f33)."""

    SCRIPT = REPO / "scripts/cloud/github-env.sh"

    def github_env(self, env_dir: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self.SCRIPT)], capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "AGENT_GCP_ENV_DIR": str(env_dir)},
        )

    def test_values_are_read_as_the_shell_reads_them(self):
        env_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, env_dir)
        (env_dir / "project.env").write_text(
            "# a comment\n"
            "PROJECT_ID=p\n"
            "ZONE=\"us-central1-a\"\n"
            "NETWORK='projects/p/global/networks/a b'\n"
            "SUBNET=two\\ words\n"
            "NOTE=$'line one\\nline two'\n"
        )
        result = self.github_env(env_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parse_github_env(result.stdout), {
            "PROJECT_ID": "p",
            "ZONE": "us-central1-a",
            "NETWORK": "projects/p/global/networks/a b",
            "SUBNET": "two words",
            "NOTE": "line one\nline two",
        })

    def test_the_real_project_env(self):
        result = self.github_env(REPO / "infra/gcp")
        self.assertEqual(result.returncode, 0, result.stderr)
        values = parse_github_env(result.stdout)
        self.assertEqual(values["PROJECT_ID"], "lolay-agents")
        self.assertIn("WIF_PROVIDER", values)
        self.assertIn("CI_SERVICE_ACCOUNT", values)

    def test_a_missing_project_env_fails(self):
        env_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, env_dir)
        result = self.github_env(env_dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no ", result.stderr)

    def test_workflows_read_project_env_only_through_it(self):
        # Any other mention (grep, sed, cat, source) is a second reader that can
        # drift from the shell's reading; the helper is the one (r1.f33). A paths
        # filter entry naming the file reads nothing.
        path_entry = re.compile(r"""^\s*-\s*["']?infra/gcp/project\.env["']?\s*$""")
        for workflow in sorted((REPO / ".github/workflows").glob("*.yml")):
            for number, line in enumerate(workflow.read_text().splitlines(), 1):
                if "project.env" in line and not path_entry.match(line):
                    self.fail(f"{workflow.name}:{number} reads project.env itself; "
                              f"use scripts/cloud/github-env.sh: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
