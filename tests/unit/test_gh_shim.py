"""Claude Code's gh shim stays first on PATH: images/shared/profile-gh-shim.sh,
the Android profile that sources it, the shared SessionStart hook that has every
Bash tool command source the profile, and the cloud settings merge that keeps
that hook beside the cloud one. The cloud profile's cases are in
test_entrypoint.ProfileTests."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, write_executable

GH_SHIM_PROFILE = REPO / "images/shared/profile-gh-shim.sh"
ANDROID_PROFILE = REPO / "images/android/files/profile.sh"
CLOUD_PROFILE = REPO / "images/cloud/runner/profile.sh"
HOOK = REPO / "images/shared/claude/hooks/session-profile.sh"
SHARED_SETTINGS = REPO / "images/shared/claude/settings.json"
CLOUD_SETTINGS = REPO / "images/cloud/runner/claude/settings.json"
MERGE = REPO / "images/cloud/runner/merge-settings.sh"
ANDROID_BREW = "/home/linuxbrew/.linuxbrew"


def run_sh(script, env, shell="sh"):
    # stdin from /dev/null: see test_entrypoint.ProfileTests.path_after.
    result = subprocess.run(
        [f"/bin/{shell}", "-c", script], env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


class ShimCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.config = self.tmp / "claude-config"
        self.shim = self.config / "ccr/bin"

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def write_shim(self, *, executable=True):
        gh = write_executable(self.shim / "gh", "echo shim\n")
        if not executable:
            gh.chmod(0o644)

    def env(self, path, **extra):
        return {"HOME": str(self.home), "PATH": path, "CLAUDE_CONFIG_DIR": str(self.config),
                "AGENT_GH_SHIM_PROFILE": str(GH_SHIM_PROFILE), **extra}


class GhShimProfileTests(ShimCase):
    def path_after(self, start, times=1, **extra):
        script = '. "$HELPER"; ' * times + 'printf %s "$PATH"'
        return run_sh(script, self.env(start, HELPER=str(GH_SHIM_PROFILE), **extra))

    def test_moves_the_shim_to_the_front_once(self):
        self.write_shim()
        shim = str(self.shim)
        for start in ("/usr/bin:/bin", f"/opt/brew/bin:{shim}:/usr/bin:{shim}"):
            for times in (1, 3):
                self.assertEqual(self.path_after(start, times), f"{shim}:/opt/brew/bin:/usr/bin"
                                 if "brew" in start else f"{shim}:/usr/bin:/bin")

    def test_leaves_a_path_that_starts_with_it(self):
        self.write_shim()
        start = f"{self.shim}:/usr/bin::/bin"
        self.assertEqual(self.path_after(start), start)

    def test_keeps_the_rest_as_it_was(self):
        # Empty entries (the current directory) and order survive; nothing is added
        # after the shim when the rest is empty.
        self.write_shim()
        self.assertEqual(self.path_after(f"/a::/b:{self.shim}"), f"{self.shim}:/a::/b")
        self.assertEqual(self.path_after(str(self.shim)), str(self.shim))

    def test_no_executable_shim_no_change(self):
        self.write_shim(executable=False)
        self.assertEqual(self.path_after("/usr/bin:/bin"), "/usr/bin:/bin")
        self.assertEqual(self.path_after("/usr/bin:/bin", CLAUDE_CONFIG_DIR=""), "/usr/bin:/bin")


class AndroidProfileTests(ShimCase):
    """images/android/files/profile.sh puts Homebrew first on every source, then the
    shim ahead of it."""

    def path_after(self, start, times=1, **extra):
        script = '. "$PROFILE"; ' * times + 'printf %s "$PATH"'
        return run_sh(script, self.env(start, PROFILE=str(ANDROID_PROFILE), **extra)).split(":")

    def test_shim_goes_ahead_of_homebrew(self):
        self.write_shim()
        for times in (1, 3):
            path = self.path_after(f"{ANDROID_BREW}/bin:{self.shim}:/usr/bin:/bin", times)
            self.assertEqual(path[:2], [str(self.shim), f"{self.home}/bin"])
            self.assertEqual(path.count(str(self.shim)), 1)
            self.assertLess(path.index(f"{ANDROID_BREW}/bin"), path.index("/usr/bin"))

    def test_no_shim_no_change(self):
        self.write_shim(executable=False)
        path = self.path_after("/usr/bin:/bin")
        self.assertEqual(path[0], f"{self.home}/bin")
        self.assertNotIn(str(self.shim), path)

    def test_a_missing_helper_is_skipped(self):
        # An image built before the helper existed, or the cloud profile's tests.
        self.write_shim()
        path = self.path_after("/usr/bin:/bin", AGENT_GH_SHIM_PROFILE=str(self.tmp / "absent"))
        self.assertEqual(path[0], f"{self.home}/bin")

    def test_bash_tool_command_finds_the_shim(self):
        # Replays a Bash tool command on an Android VM: the runner's PATH comes from
        # `bash -l` (Homebrew first), Claude Code puts the shim ahead of it, the
        # shell snapshot's `export PATH=` drops it again, and then the env file the
        # hook wrote is sourced. A signed-out gh in ~/bin stands in for Homebrew's.
        self.write_shim()
        write_executable(self.home / "bin/gh", "echo signed-out\n")
        env_file = self.tmp / "claude-env"
        env_file.touch()
        run_hook(self.tmp, CLAUDE_ENV_FILE=str(env_file), AGENT_PROFILE=str(ANDROID_PROFILE))
        # The runner's login shell started before the session, so without the shim.
        login_path = ":".join(self.path_after("/usr/bin:/bin", CLAUDE_CONFIG_DIR=""))
        self.assertNotIn(str(self.shim), login_path)
        command = f'export PATH={login_path}; . "$CLAUDE_ENV_FILE"; gh; bash -c gh'
        env = self.env(f"{self.shim}:{login_path}", CLAUDE_ENV_FILE=str(env_file))
        out = run_sh(command, env, shell="bash").split()
        self.assertEqual(out, ["shim", "shim"])


class CloudBashToolTests(ShimCase):
    """A Bash tool command on the cloud image, where BASH_ENV re-sources the
    profile in every bash (images/cloud/runner/profile.sh)."""

    def test_bash_tool_command_finds_the_shim_and_a_childs_fake(self):
        # Claude Code's process has the entrypoint's PATH and marker. Its Bash tool
        # puts the shim first, BASH_ENV leaves that alone, the snapshot's `export
        # PATH=` drops the shim, and the env file's line puts it back. A command
        # that then puts a fake gh first reaches the fake in a child bash.
        self.write_shim()
        write_executable(self.home / "bin/gh", "echo signed-out\n")
        fake = self.tmp / "fake"
        write_executable(fake / "gh", "echo fake\n")
        env_file = self.tmp / "claude-env"
        env_file.touch()
        run_hook(self.tmp, CLAUDE_ENV_FILE=str(env_file), AGENT_PROFILE=str(CLOUD_PROFILE))
        cloud = {"HOMEBREW_PREFIX": str(self.tmp / "brew"), "AGENT_SCRATCH": str(self.tmp),
                 "AGENT_TOOLSETS_DIR": str(self.tmp / "toolsets")}
        entry = self.env("/usr/bin:/bin", CLAUDE_CONFIG_DIR="", **cloud)
        entry_path = run_sh(f'. "{CLOUD_PROFILE}"; printf %s "$PATH"', entry)
        command = (f'export PATH={entry_path}; . "$CLAUDE_ENV_FILE"; gh; bash -c gh; '
                   f'PATH="{fake}:$PATH" bash -c gh')
        env = self.env(f"{self.shim}:{entry_path}", CLAUDE_ENV_FILE=str(env_file),
                       BASH_ENV=str(CLOUD_PROFILE), AGENT_PROFILE_PATH_DONE="1", **cloud)
        out = run_sh(command, env, shell="bash").split()
        self.assertEqual(out, ["shim", "shim", "fake"])


def run_hook(tmp, payload='{"source": "startup"}', **env):
    result = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True, timeout=30,
        env={"HOME": str(tmp), "PATH": "/usr/bin:/bin", **env},
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


class SessionProfileHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env_file = self.tmp / "claude-env"
        self.env_file.write_text("export AGENT_OUTPUTS=/mnt/outputs/x\n")
        self.profile = self.tmp / "profile dir/agent-images.sh"
        self.profile.parent.mkdir()
        self.profile.write_text("")

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def test_bash_commands_source_the_profile_once(self):
        # A resumed session runs the hook again on the same env file.
        for _ in range(2):
            self.assertEqual(run_hook(self.tmp, CLAUDE_ENV_FILE=str(self.env_file),
                                      AGENT_PROFILE=str(self.profile)), "")
        lines = self.env_file.read_text().splitlines()
        self.assertEqual(lines[0], "export AGENT_OUTPUTS=/mnt/outputs/x")
        self.assertEqual(lines[1:], [f"unset AGENT_PROFILE_PATH_DONE; . {str(self.profile).replace(' ', chr(92) + ' ')}"])
        # The line sources the profile, space in the path and all.
        out = run_sh(f'. "{self.env_file}"; echo ok', {"PATH": "/usr/bin:/bin"}, shell="bash")
        self.assertEqual(out, "ok\n")

    def test_no_profile_no_line(self):
        # macOS: the shared settings run the hook there too, with no such profile.
        run_hook(self.tmp, CLAUDE_ENV_FILE=str(self.env_file), AGENT_PROFILE=str(self.tmp / "absent"))
        self.assertEqual(self.env_file.read_text(), "export AGENT_OUTPUTS=/mnt/outputs/x\n")

    def test_no_env_file_is_fine(self):
        self.assertEqual(run_hook(self.tmp, AGENT_PROFILE=str(self.profile)), "")


class SettingsTests(unittest.TestCase):
    def commands(self, settings):
        return {event: [hook["command"] for entry in entries for hook in entry["hooks"]]
                for event, entries in settings.get("hooks", {}).items()}

    def merge(self, shared, overlay):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "a.json").write_text(json.dumps(shared))
            (tmp / "b.json").write_text(json.dumps(overlay))
            return subprocess.run(["bash", str(MERGE), str(tmp / "a.json"), str(tmp / "b.json")],
                                  capture_output=True, text=True, timeout=30)
        finally:
            subprocess.run(["rm", "-rf", str(tmp)], check=True)

    def test_shared_settings_run_the_session_profile_hook(self):
        # Android and macOS install the shared settings as they are.
        hooks = self.commands(json.loads(SHARED_SETTINGS.read_text()))
        self.assertEqual(hooks["SessionStart"], ['"$CLAUDE_CONFIG_DIR/hooks/session-profile.sh"'])
        self.assertTrue(HOOK.stat().st_mode & 0o111)

    def test_cloud_image_runs_both_session_start_hooks(self):
        result = subprocess.run(["bash", str(MERGE), str(SHARED_SETTINGS), str(CLOUD_SETTINGS)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        hooks = self.commands(json.loads(result.stdout))
        self.assertEqual(hooks["SessionStart"], ['"$CLAUDE_CONFIG_DIR/hooks/session-profile.sh"',
                                                 '"$CLAUDE_CONFIG_DIR/hooks/session-start.sh"'])
        self.assertEqual(hooks["Stop"], ['"$CLAUDE_CONFIG_DIR/hooks/stop-push-reminder.sh"'])

    def test_hook_lists_concatenate_and_other_keys_merge(self):
        entry = lambda name: {"hooks": [{"type": "command", "command": name}]}  # noqa: E731
        result = self.merge(
            {"env": {"A": "1", "B": "1"}, "hooks": {"SessionStart": [entry("a")], "Stop": [entry("s")]}},
            {"env": {"B": "2"}, "model": "x", "hooks": {"SessionStart": [entry("b")], "PreToolUse": [entry("p")]}},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        merged = json.loads(result.stdout)
        self.assertEqual(merged["env"], {"A": "1", "B": "2"})
        self.assertEqual(merged["model"], "x")
        self.assertEqual(self.commands(merged), {"SessionStart": ["a", "b"], "Stop": ["s"], "PreToolUse": ["p"]})
        self.assertNotIn("hooks", json.loads(self.merge({"a": 1}, {"b": 2}).stdout))

    def test_rejects_bad_input(self):
        result = subprocess.run(["bash", str(MERGE), str(SHARED_SETTINGS)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "bad.json").write_text("{not json")
            result = subprocess.run(["bash", str(MERGE), str(SHARED_SETTINGS), str(tmp / "bad.json")],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
        finally:
            subprocess.run(["rm", "-rf", str(tmp)], check=True)


if __name__ == "__main__":
    unittest.main()
