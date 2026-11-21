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


class RunnerSettingsTests(unittest.TestCase):
    """gcp_runner_settings' CLAUDE_UPGRADE=false: only where the watch locks
    Claude, a nightly Cloud Run type (D6, r3.f9)."""

    def upgrade(self, text: str):
        env_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, env_dir)
        (env_dir / "project.env").write_text("PROJECT_ID=p\n")
        (env_dir / "t.env").write_text(text)
        result = subprocess.run(
            ["bash", "-c", f'set -euo pipefail; source "{REPO}/hooks/gcp/lib.sh"; gcp_load_env t; gcp_runner_settings t'],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "AGENT_GCP_ENV_DIR": str(env_dir)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = dict(line.split("=", 1) for line in result.stdout.splitlines())
        return settings.get("CLAUDE_UPGRADE")

    def test_a_nightly_cloud_run_type_runs_its_locked_claude(self):
        self.assertEqual(self.upgrade("REBUILD=nightly\n"), "false")
        self.assertEqual(self.upgrade(""), "false", "unset is nightly, and BACKEND's default is run")
        self.assertEqual(self.upgrade("BACKEND=run\n"), "false")

    def test_a_weekly_type_updates_itself(self):
        self.assertIsNone(self.upgrade("REBUILD=weekly\n"))
        self.assertIsNone(self.upgrade("BACKEND=compute\nREBUILD=weekly\n"))

    def test_a_compute_type_updates_itself_whatever_its_class(self):
        # The watch resolves only Cloud Run types: a nightly compute type's Claude
        # is its last Packer build's, so it must keep updating itself at start.
        self.assertIsNone(self.upgrade("BACKEND=compute\nREBUILD=nightly\n"))
        self.assertIsNone(self.upgrade("BACKEND=compute\n"), "no REBUILD is nightly")


# The project keys the Owner decided a type's env file may not set (#161). The
# guard reads its list from project.env (gcp_project_keys); this only checks
# that list still holds them.
DECIDED_PROJECT_KEYS = ("PROJECT_ID", "PROJECT_NUMBER", "REGION", "ZONE", "NETWORK", "SUBNET", "ARTIFACT_REGISTRY")


def project_keys() -> list[str]:
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{REPO}/hooks/gcp/lib.sh"; gcp_project_keys'],
        capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "AGENT_GCP_ENV_DIR": str(REPO / "infra/gcp")},
    )
    return result.stdout.split()


class CheckTypeTests(unittest.TestCase):
    """gcp_check_type: the one reader of the rules for what a type's env file may
    set (DEPLOY=false on compute, IMAGE_FAMILY, the project keys)."""

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

    def test_refuses_a_project_key_in_any_env_file(self):
        # deploy.sh and the reaper read project.env alone, so a type's override of
        # a project key would reach spawn-runner but not them (#161).
        keys = project_keys()
        for decided in DECIDED_PROJECT_KEYS:
            self.assertIn(decided, keys, "a key the Owner refused in #161 left project.env")
        for key in keys:
            with self.subTest(key=key):
                env_dir = Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, env_dir)
                shutil.copytree(REPO / "infra/gcp", env_dir, dirs_exist_ok=True)
                types = sorted(path.stem for path in env_dir.glob("*.env") if path.stem != "project")
                for type_ in types:
                    with (env_dir / f"{type_}.env").open("a") as file:
                        file.write(f"{key}=other\n")
                refused = self.refused_types(env_dir)
                self.assertEqual(sorted(refused), types)
                for type_, message in refused.items():
                    self.assertEqual(
                        message,
                        f"{type_}: {key} is a project key: it belongs in infra/gcp/project.env only, which "
                        f"the reaper and Cloud Run deploys read alone; remove it from infra/gcp/{type_}.env\n",
                    )

    def test_refuses_a_project_key_set_empty_or_exported(self):
        env_dir = self.fixture(empty="ZONE=\n", exported="export REGION=us-east1\n")
        (env_dir / "project.env").write_text("PROJECT_ID=p\nREGION=r\nZONE=z\n")
        for type_, key in (("empty", "ZONE"), ("exported", "REGION")):
            with self.subTest(type=type_):
                result = self.check(env_dir, type_)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.startswith(f"{type_}: {key} is a project key"), result.stderr)

    def test_a_line_that_reads_a_key_or_fails_does_not_hide_a_set_key(self):
        # spawn-runner's gcp_load_env sources without -u and ignores the status,
        # so it keeps REGION from both files; the check must see it too.
        env_dir = self.fixture(
            reads="SA=runner@${PROJECT_ID}.iam\nREGION=us-east1\n",
            fails="REGION=us-east1\nfalse\n",
        )
        (env_dir / "project.env").write_text("PROJECT_ID=p\nREGION=r\n")
        for type_ in ("reads", "fails"):
            with self.subTest(type=type_):
                result = self.check(env_dir, type_)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.startswith(f"{type_}: REGION is a project key"), result.stderr)

    def test_an_override_that_fires_only_on_project_env_values_is_refused(self):
        # Sourced with the keys unset, the condition is false; gcp_load_env
        # sources it over project.env, where it moves REGION (Copilot on #162).
        env_dir = self.fixture(conditional='if [[ "${REGION:-}" == r ]]; then REGION=us-east1; fi\n')
        (env_dir / "project.env").write_text("PROJECT_ID=p\nREGION=r\n")
        result = self.check(env_dir, "conditional")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("conditional: REGION is a project key"), result.stderr)

    def test_project_keys_are_what_project_env_sets_however_written(self):
        env_dir = self.fixture()
        (env_dir / "project.env").write_text("# comment\nPROJECT_ID=p\n  NETWORK=n\nexport A=1 B=2\n")
        result = subprocess.run(
            ["bash", "-c", f'set -euo pipefail; source "{REPO}/hooks/gcp/lib.sh"; gcp_project_keys'],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "AGENT_GCP_ENV_DIR": str(env_dir)},
        )
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(result.stdout.split(), ["A", "B", "NETWORK", "PROJECT_ID"])

    def test_project_keys_already_loaded_do_not_count_as_the_types(self):
        # The supervisor and deploy.sh have project.env exported when they call it.
        script = f'set -euo pipefail; source "{REPO}/hooks/gcp/lib.sh"; gcp_load_env'
        for type_ in sorted(path.stem for path in (REPO / "infra/gcp").glob("*.env") if path.stem != "project"):
            with self.subTest(type=type_):
                result = subprocess.run(
                    ["bash", "-c", f"{script}; gcp_check_type {type_}"],
                    capture_output=True, text=True,
                    env={"PATH": "/usr/bin:/bin", "AGENT_GCP_ENV_DIR": str(REPO / "infra/gcp")},
                )
                self.assertEqual((result.returncode, result.stderr), (0, ""))

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

    def test_a_named_file_is_read_the_same_way(self):
        # tools.env goes through it too (ci.yml, cloud-build, the deploy).
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        env_file = tmp / "tools.env"
        env_file.write_text("# renovate: datasource=x depName=y\nPACKER_VERSION=\"1.16.2\"\nexport SHFMT_VERSION='3.14.1'\n")
        result = subprocess.run(["bash", str(self.SCRIPT), str(env_file)], capture_output=True, text=True,
                                env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parse_github_env(result.stdout), {"PACKER_VERSION": "1.16.2", "SHFMT_VERSION": "3.14.1"})
        self.assertEqual(self.github_env_args(str(tmp / "missing.env")).returncode, 1)
        self.assertEqual(self.github_env_args("a", "b").returncode, 2)

    def github_env_args(self, *args):
        return subprocess.run(["bash", str(self.SCRIPT), *args], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin"})

    def test_it_stays_bash_3_2_where_macos_runs_it(self):
        # ci.yml runs it on macOS, whose /bin/bash is 3.2: no bash 4 builtins or
        # expansions, and the GCP library only for project.env.
        text = "\n".join(line for line in self.SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#"))
        for bash4 in (r"\bmapfile\b", r"\breadarray\b", r"declare\s+-A", r"local\s+-A", r"\$\{[^}]*(,,|\^\^)", r"\bcoproc\b"):
            with self.subTest(construct=bash4):
                self.assertNotRegex(text, bash4)

    def test_no_workflow_greps_an_env_file(self):
        # Every env file reaches $GITHUB_ENV through the helper (r1.f33), tools.env
        # included: a grep keeps a quoted value's quotes.
        for workflow in sorted((REPO / ".github/workflows").glob("*.yml")):
            for number, line in enumerate(workflow.read_text().splitlines(), 1):
                if ".env" in line and "GITHUB_ENV" in line and "github-env.sh" not in line:
                    self.fail(f"{workflow.name}:{number} loads an env file itself; "
                              f"use scripts/cloud/github-env.sh: {line.strip()}")

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
