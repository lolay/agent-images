"""images/cloud/entrypoint.sh, run outside a container with fake commands."""

import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from support import REPO, base_env, read_env_file, write_executable

ENTRYPOINT = REPO / "images/cloud/entrypoint.sh"
PROFILE = REPO / "images/cloud/runner/profile.sh"
TOOLCHAINS = (
    "node", "pnpm", "python3", "uv", "go", "cargo", "rustc", "protoc", "buf",
    "grpcurl", "terraform", "tflint", "actionlint", "shellcheck", "mandoc", "java",
    "cmake", "pkg-config", "gh", "sops", "age", "rg", "tmux",
)


class EntrypointCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.scratch = self.tmp / "scratch"
        self.datasets = self.tmp / "datasets"
        self.outputs = self.tmp / "outputs"
        self.out = self.tmp / "out"
        self.fake_bin = self.tmp / "bin"
        for path in (self.home, self.scratch, self.datasets, self.outputs, self.out):
            path.mkdir()
        # The runner the entrypoint execs: record what it was given.
        self.runner = write_executable(
            self.fake_bin / "fake-runner",
            'env >"$FAKE_OUT/env"\n'
            'cp "$HOME/.config/agent-runner/runner.env" "$FAKE_OUT/runner.env"\n'
            'sleep "${FAKE_RUNNER_SLEEP:-0}"\n',
        )
        self.watchdog = write_executable(
            self.fake_bin / "fake-watchdog", 'echo ran >>"$FAKE_OUT/watchdog"\n'
        )
        write_executable(self.fake_bin / "claude", "echo '9.9.9 (Claude Code)'\n")
        # The toolchains the smoke checks; FAKE_BROKEN_TOOL names one that fails,
        # as a rustup or tenv proxy does when its toolchain isn't installed.
        for tool in TOOLCHAINS:
            write_executable(
                self.fake_bin / tool,
                f'[[ "${{FAKE_BROKEN_TOOL:-}}" != {tool} ]] || exit 1\necho "{tool} 9.9"\n',
            )
        # What install-toolset.sh records: two toolsets whose checks call the
        # fakes, and one with no checks.
        self.toolsets = self.tmp / "toolsets"
        self.toolsets.mkdir()
        half = len(TOOLCHAINS) // 2
        (self.toolsets / "toolsets.txt").write_text("first\nbrowser\nsecond\n")
        for name, tools in (("first", TOOLCHAINS[:half]), ("second", TOOLCHAINS[half:])):
            (self.toolsets / f"{name}.check").write_text(
                "# a comment\n\n" + "".join(f"{tool}: {tool} --version\n" for tool in tools)
            )
        self.brew_prefix = self.tmp / "linuxbrew"
        for sub in ("Cellar", "bin"):
            (self.brew_prefix / sub).mkdir(parents=True)
        write_executable(
            self.fake_bin / "brew",
            'if [[ "$1" == --prefix ]]; then echo "$FAKE_BREW_PREFIX"; else echo "Homebrew 9.9"; fi\n',
        )

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def run_entrypoint(self, *args, **env):
        settings = {
            "AGENT_SCRATCH": str(self.scratch),
            "AGENT_DATASETS": str(self.datasets),
            "AGENT_OUTPUTS_ROOT": str(self.outputs),
            "AGENT_PROFILE": str(PROFILE),
            "AGENT_TOOLSETS_DIR": str(self.toolsets),
            "AGENT_RUNNER_COMMAND": str(self.runner),
            "AGENT_WATCHDOG_COMMAND": str(self.watchdog),
            # The loop outlives the runner by at most one interval, holding the pipe.
            "AGENT_WATCHDOG_INTERVAL_SEC": "0.1",
            "AGENT_REQUIRE_SCRATCH_MOUNT": "false",
            "AGENT_TYPE": "linux",
            "FAKE_OUT": str(self.out),
            "FAKE_BREW_PREFIX": str(self.brew_prefix),
            # profile.sh puts Homebrew ahead of the fakes; a real one on the host
            # (GitHub's runners have one) would shadow them.
            "HOMEBREW_PREFIX": str(self.brew_prefix),
            "SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET": "work-order-jwt",
            "RUNNER_LABEL": "linux-abc123",
        }
        settings.update(env)
        settings = {key: value for key, value in settings.items() if value is not None}
        return subprocess.run(
            ["bash", str(ENTRYPOINT), *args],
            env=base_env(self.home, self.fake_bin, **settings),
            stdin=subprocess.DEVNULL,  # see ProfileTests.path_after
            capture_output=True,
            text=True,
            timeout=30,
        )

    def runner_env(self) -> dict:
        return dict(
            line.split("=", 1)
            for line in (self.out / "env").read_text().splitlines()
            if "=" in line
        )


class RunTests(EntrypointCase):
    def test_points_temp_caches_and_checkouts_at_scratch(self):
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self.runner_env()
        self.assertEqual(env["TMPDIR"], f"{self.scratch}/tmp")
        self.assertEqual(env["XDG_CACHE_HOME"], f"{self.scratch}/cache")
        self.assertEqual(env["PIP_CACHE_DIR"], f"{self.scratch}/cache/pip")
        self.assertEqual(env["HOMEBREW_CACHE"], f"{self.scratch}/cache/Homebrew")
        self.assertEqual(env["CARGO_HOME"], f"{self.scratch}/cache/cargo")
        self.assertEqual(env["pnpm_config_store_dir"], f"{self.scratch}/cache/pnpm-store")
        path = env["PATH"].split(":")
        self.assertLess(path.index(f"{self.brew_prefix}/bin"), path.index("/usr/bin"))
        self.assertLess(
            path.index(f"{self.brew_prefix}/opt/rustup/bin"), path.index(f"{self.brew_prefix}/bin")
        )
        self.assertEqual(path[-1], f"{self.scratch}/cache/cargo/bin")
        self.assertEqual(env["CLAUDE_BASE_DIR"], f"{self.scratch}/workspace")
        self.assertEqual(env["AGENT_SCRATCH"], str(self.scratch))
        self.assertEqual(env["AGENT_DATASETS"], str(self.datasets))
        for sub in ("workspace", "tmp", "cache"):
            self.assertTrue((self.scratch / sub).is_dir(), sub)

    def test_moves_the_work_order_to_an_owner_only_file(self):
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET", self.runner_env())
        secret = self.home / ".claude-runner/environment-secret"
        self.assertEqual(secret.read_text(), "work-order-jwt")
        self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)
        self.assertNotIn("work-order-jwt", result.stdout + result.stderr)
        self.assertNotIn("work-order-jwt", (self.out / "runner.env").read_text())

    def test_writes_runner_env_for_agent_runner(self):
        result = self.run_entrypoint(
            CLAUDE_RELEASE_IDLE_SESSION_MIN="60",
            CLAUDE_KILL_SESSION_AFTER_MIN="480",
            CLAUDE_EXIT_IF_UNUSED_MIN="60",
            CLAUDE_UPGRADE="true",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        runner_env = read_env_file(self.out / "runner.env")
        self.assertEqual(runner_env["AGENT"], "claude")
        self.assertEqual(runner_env["RUNNER_LABEL"], "linux-abc123")
        self.assertEqual(runner_env["CLAUDE_BASE_DIR"], f"{self.scratch}/workspace")
        self.assertEqual(runner_env["CLAUDE_RELEASE_IDLE_SESSION_MIN"], "60")
        self.assertEqual(runner_env["CLAUDE_KILL_SESSION_AFTER_MIN"], "480")
        self.assertEqual(runner_env["CLAUDE_EXIT_IF_UNUSED_MIN"], "60")
        self.assertEqual(runner_env["CLAUDE_UPGRADE"], "true")
        self.assertNotIn("CLAUDE_RETIRE_AT", runner_env)
        mode = stat.S_IMODE((self.home / ".config/agent-runner/runner.env").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_retires_the_margin_before_the_task_timeout(self):
        before = int(time.time())
        result = self.run_entrypoint(AGENT_TASK_TIMEOUT_SEC="34200", AGENT_RETIRE_MARGIN_SEC="900")
        after = int(time.time())
        self.assertEqual(result.returncode, 0, result.stderr)
        retire_at = int(read_env_file(self.out / "runner.env")["CLAUDE_RETIRE_AT"])
        self.assertGreaterEqual(retire_at, before + 34200 - 900)
        self.assertLessEqual(retire_at, after + 34200 - 900)
        self.assertEqual(self.runner_env()["AGENT_RETIRE_AT"], str(retire_at))

    def test_label_defaults_to_type_and_execution(self):
        result = self.run_entrypoint(RUNNER_LABEL=None, CLOUD_RUN_EXECUTION="agent-linux-x7k2p")
        self.assertEqual(result.returncode, 0, result.stderr)
        runner_env = read_env_file(self.out / "runner.env")
        self.assertEqual(runner_env["RUNNER_LABEL"], "linux-agent-linux-x7k2p")

    def test_runs_the_watchdog_beside_the_runner(self):
        result = self.run_entrypoint(FAKE_RUNNER_SLEEP="0.6")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(len((self.out / "watchdog").read_text().splitlines()), 2)


class RefusalTests(EntrypointCase):
    def test_refuses_without_a_work_order(self):
        result = self.run_entrypoint(SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET=None)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no work order", result.stderr)
        self.assertFalse((self.out / "env").exists())

    def test_refuses_when_scratch_is_not_a_mount(self):
        result = self.run_entrypoint(AGENT_REQUIRE_SCRATCH_MOUNT="true")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a mount", result.stderr)
        self.assertFalse((self.out / "env").exists())

    def test_refuses_a_timeout_inside_the_margin(self):
        result = self.run_entrypoint(AGENT_TASK_TIMEOUT_SEC="600", AGENT_RETIRE_MARGIN_SEC="900")
        self.assertEqual(result.returncode, 1)
        self.assertIn("margin", result.stderr)


class SmokeTests(EntrypointCase):
    def test_reports_every_check_and_fails_on_any(self):
        # A temp dir is no mount, and root (or a writable dir) can write datasets.
        result = self.run_entrypoint("smoke", SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET=None)
        self.assertEqual(result.returncode, 1)
        lines = [line for line in result.stdout.splitlines() if line.startswith("smoke: ")]
        checks = {line.split()[2]: line.split()[1] for line in lines}
        self.assertEqual(
            set(checks),
            {"claude", "toolchains", "scratch", "outputs", "datasets", "homebrew"},
        )
        self.assertEqual(checks["claude"], "pass")
        self.assertEqual(checks["toolchains"], "pass")
        self.assertEqual(checks["outputs"], "pass")
        self.assertEqual(checks["homebrew"], "pass")
        self.assertEqual(checks["scratch"], "FAIL")
        self.assertEqual(checks["datasets"], "FAIL")
        self.assertEqual(list((self.outputs / "_smoke").iterdir()), [])
        self.assertFalse((self.out / "env").exists(), "smoke must not start the runner")

    def test_a_toolchain_that_fails_fails_the_check(self):
        result = self.run_entrypoint("smoke", FAKE_BROKEN_TOOL="cargo")
        line = next(line for line in result.stdout.splitlines() if " toolchains " in line)
        self.assertTrue(line.startswith("smoke: FAIL toolchains"), line)
        self.assertIn("cargo: missing", line)
        self.assertIn("node: node 9.9", line)

    def test_a_failed_check_says_why(self):
        (self.toolsets / "first.check").write_text("node: echo 'node: not found' >&2; exit 1\n")
        result = self.run_entrypoint("smoke")
        line = next(line for line in result.stdout.splitlines() if " toolchains " in line)
        self.assertIn("node: missing (node: not found)", line)

    def test_checks_can_use_tmpdir_on_an_empty_scratch(self):
        # Cloud Run mounts scratch empty, and the display toolset's screenshot
        # check writes with mktemp before the scratch check runs.
        (self.toolsets / "toolsets.txt").write_text("display\n")
        (self.toolsets / "display.check").write_text(
            'screenshot: f="$(mktemp --suffix=.png)" && echo "wrote $f"; rc=$?; rm -f "$f"; exit $rc\n'
        )
        self.assertFalse((self.scratch / "tmp").exists())
        result = self.run_entrypoint("smoke")
        line = next(line for line in result.stdout.splitlines() if " toolchains " in line)
        self.assertTrue(line.startswith("smoke: pass toolchains"), line)
        self.assertIn(f"screenshot: wrote {self.scratch}/tmp/", line)

    def test_homebrew_must_be_writable_by_the_session_user(self):
        result = self.run_entrypoint("smoke", FAKE_BREW_PREFIX=str(self.tmp / "missing"))
        line = next(line for line in result.stdout.splitlines() if " homebrew " in line)
        self.assertTrue(line.startswith("smoke: FAIL homebrew"), line)
        self.assertIn("isn't writable", line)


    def check_line(self, result, name):
        return next(line for line in result.stdout.splitlines() if f" {name} " in line)

    def test_an_unwritable_outputs_root_fails_the_smoke(self):
        # A path under /dev/null can't be created, even by root.
        result = self.run_entrypoint("smoke", AGENT_OUTPUTS_ROOT="/dev/null/broken")
        self.assertEqual(result.returncode, 1)
        line = self.check_line(result, "outputs")
        self.assertTrue(line.startswith("smoke: FAIL outputs"), line)
        self.assertNotIn("wrote, read and deleted", line)

    def test_a_missing_datasets_mount_fails_the_smoke(self):
        result = self.run_entrypoint("smoke", AGENT_DATASETS=str(self.tmp / "missing"))
        self.assertEqual(result.returncode, 1)
        line = self.check_line(result, "datasets")
        self.assertTrue(line.startswith("smoke: FAIL datasets"), line)
        self.assertNotIn("a write was refused", line)

    def test_a_scratch_disk_that_cant_be_written_fails_the_check(self):
        # dd's failure mid-function must decide the check (the mount is faked).
        write_executable(self.fake_bin / "mountpoint", "exit 0\n")
        write_executable(self.fake_bin / "findmnt", "echo ext4\n")
        write_executable(self.fake_bin / "dd", "echo 'dd: no space left' >&2\nexit 1\n")
        result = self.run_entrypoint("smoke")
        line = self.check_line(result, "scratch")
        self.assertTrue(line.startswith("smoke: FAIL scratch"), line)


class DisplayTests(EntrypointCase):
    """Images with the display toolset get Xvfb on $DISPLAY before the runner or smoke."""

    def fake_display(self, *, answers=True):
        # Xvfb records its arguments; xdpyinfo answers once Xvfb has "started".
        write_executable(self.fake_bin / "Xvfb", 'printf "%s\\n" "$*" >"$FAKE_OUT/xvfb"\n')
        answer = '[[ -f "$FAKE_OUT/xvfb" ]]' if answers else "exit 1"
        write_executable(self.fake_bin / "xdpyinfo", answer + "\n")

    def test_starts_xvfb_on_the_display_before_the_runner(self):
        self.fake_display()
        result = self.run_entrypoint(DISPLAY=":99")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.out / "xvfb").read_text().split()[:4], [":99", "-screen", "0", "1920x1080x24"])
        self.assertIn("display :99", result.stdout)
        self.assertTrue((self.out / "env").exists(), "the runner still starts")

    def test_smoke_starts_it_too(self):
        self.fake_display()
        self.run_entrypoint("smoke", DISPLAY=":99")
        self.assertTrue((self.out / "xvfb").exists())

    def test_no_display_no_xvfb(self):
        self.fake_display()
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.out / "xvfb").exists())

    def test_a_display_that_never_answers_warns_and_the_runner_still_starts(self):
        self.fake_display(answers=False)
        result = self.run_entrypoint(DISPLAY=":99", AGENT_DISPLAY_WAIT_SEC="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Xvfb didn't answer on :99", result.stderr)
        self.assertTrue((self.out / "env").exists())


class ProfileTests(unittest.TestCase):
    """profile.sh puts the session's tools first however the shell started."""

    HOME = "/home/ubuntu"
    BREW = "/opt/brew"

    def setUp(self):
        self.toolsets = Path(tempfile.mkdtemp())

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.toolsets)], check=True)

    def path_after(self, start_path, *, shell="sh", times=1, bash_env=False, then="printf %s \"$PATH\""):
        script = ". \"$PROFILE\"; " * times + then
        env = {"HOME": self.HOME, "HOMEBREW_PREFIX": self.BREW, "PATH": start_path,
               "PROFILE": str(PROFILE), "AGENT_SCRATCH": "/scratch",
               "AGENT_TOOLSETS_DIR": str(self.toolsets)}
        command = [f"/bin/{shell}", "-c", script]
        if bash_env:
            env["BASH_ENV"] = str(PROFILE)
            command = ["/bin/bash", "-c", then]
        # stdin from /dev/null: bash whose stdin is a socket (some test runners'
        # is) takes itself for an sshd session and reads ~/.bashrc and
        # /etc/bash.bashrc instead of BASH_ENV, so the result depends on the host.
        result = subprocess.run(
            command, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split(":")

    def assert_front(self, path):
        front = [f"{self.HOME}/bin", f"{self.HOME}/.local/bin", f"{self.BREW}/opt/rustup/bin",
                 f"{self.BREW}/bin", f"{self.BREW}/sbin"]
        self.assertEqual(path[: len(front)], front)
        self.assertEqual(len(path), len(set(path)), f"duplicates in {path}")
        self.assertEqual(path[-1], "/scratch/cache/cargo/bin")

    def test_adds_homebrew_when_home_bin_is_already_there(self):
        # The first session's case: ~/bin on PATH, Homebrew not.
        self.assert_front(self.path_after(f"{self.HOME}/bin:/usr/bin:/bin"))

    def test_moves_homebrew_ahead_of_the_system(self):
        path = self.path_after(f"/usr/bin:{self.BREW}/bin:/bin")
        self.assert_front(path)
        self.assertLess(path.index(f"{self.BREW}/bin"), path.index("/usr/bin"))

    def test_sourcing_again_changes_nothing(self):
        once = self.path_after("/usr/bin:/bin")
        self.assertEqual(self.path_after("/usr/bin:/bin", times=3), once)
        self.assert_front(once)

    def test_non_interactive_bash_gets_it_through_bash_env(self):
        self.assert_front(self.path_after("/usr/bin:/bin", bash_env=True))

    def test_toolset_paths_go_after_home_and_before_homebrew(self):
        (self.toolsets / "gis.path").write_text("# the env\n/opt/gis/bin\n")
        for times in (1, 3):
            path = self.path_after(f"/usr/bin:{self.BREW}/bin:/opt/gis/bin:/bin", times=times)
            self.assertEqual(
                path[:4], [f"{self.HOME}/bin", f"{self.HOME}/.local/bin", "/opt/gis/bin", f"{self.BREW}/opt/rustup/bin"]
            )
            self.assertEqual(len(path), len(set(path)), f"duplicates in {path}")

    def test_toolset_profiles_are_sourced(self):
        (self.toolsets / "gis.profile.sh").write_text("export GIS_ENV=/opt/gis\n")
        out = self.path_after("/usr/bin:/bin", then='printf %s "$GIS_ENV"')
        self.assertEqual(out, ["/opt/gis"])
        out = self.path_after("/usr/bin:/bin", bash_env=True, then='printf %s "$GIS_ENV"')
        self.assertEqual(out, ["/opt/gis"])

if __name__ == "__main__":
    unittest.main()
