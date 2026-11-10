"""images/cloud/runner/claude/hooks/session-start.sh: outputs folder and the notice."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env

HOOK = REPO / "images/cloud/runner/claude/hooks/session-start.sh"
# 2026-11-05T02:00:00Z, 6:00 PM PST on the 4th.
NOW = 1793844000
PROFILE = "/etc/profile.d/agent-images.sh"
SOURCE_PROFILE = f". {PROFILE}\n"


class SessionStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.outputs = self.tmp / "outputs"
        self.datasets = self.tmp / "datasets"
        self.scratch = self.tmp / "scratch"
        for path in (self.outputs, self.datasets, self.scratch):
            path.mkdir()
        self.env_file = self.tmp / "claude-env"
        self.env_file.touch()

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def run_hook(self, payload=None, **env):
        settings = {
            "AGENT_SCRATCH": str(self.scratch),
            "AGENT_OUTPUTS_ROOT": str(self.outputs),
            "AGENT_DATASETS": str(self.datasets),
            "AGENT_OUTPUTS_BUCKET": "lolay-agents-outputs",
            "AGENT_TYPE": "linux",
            "AGENT_NOTICE_TZ": "America/Los_Angeles",
            "AGENT_NOW": str(NOW),
            "CLAUDE_ENV_FILE": str(self.env_file),
            "AGENT_PROFILE": PROFILE,
            "CLAUDE_CODE_REMOTE_SESSION_ID": "session_01ABC",
            "CLAUDE_RELEASE_IDLE_SESSION_MIN": "60",
            "CLAUDE_KILL_SESSION_AFTER_MIN": "480",
        }
        settings.update(env)
        settings = {key: value for key, value in settings.items() if value is not None}
        result = subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps(payload or {"session_id": "local-uuid", "source": "startup"}),
            env=base_env(self.tmp, self.tmp / "no-bin", **settings),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def context(self, output):
        return output["hookSpecificOutput"]["additionalContext"]

    def test_creates_the_session_folder_and_exports_it(self):
        output = self.run_hook()
        folder = self.outputs / "linux/2026-11-05/session_01ABC"
        self.assertTrue(folder.is_dir())
        self.assertEqual(self.env_file.read_text(), f"export AGENT_OUTPUTS={folder}\n{SOURCE_PROFILE}")
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(
            "gs://lolay-agents-outputs/linux/2026-11-05/session_01ABC", self.context(output)
        )

    def test_session_id_precedence(self):
        self.run_hook(CLAUDE_CODE_REMOTE_SESSION_ID=None, CLAUDE_CODE_SESSION_ID="cse_2")
        self.assertTrue((self.outputs / "linux/2026-11-05/cse_2").is_dir())
        output = self.run_hook(CLAUDE_CODE_REMOTE_SESSION_ID=None)
        self.assertTrue((self.outputs / "linux/2026-11-05/local-uuid").is_dir())
        self.assertIn("Session id from the hook payload.", self.context(output))

    def test_a_resumed_session_keeps_its_folder(self):
        earlier = self.outputs / "linux/2026-11-01/session_01ABC"
        earlier.mkdir(parents=True)
        self.run_hook()
        self.assertFalse((self.outputs / "linux/2026-11-05").exists())
        self.assertEqual(self.env_file.read_text(), f"export AGENT_OUTPUTS={earlier}\n{SOURCE_PROFILE}")

    def test_notice_ends_at_the_session_cap(self):
        output = self.run_hook()
        self.assertEqual(
            output["systemMessage"],
            "⚠ This session will time out after 60 minutes of inactivity, and ends by "
            "2:00 AM PST at the latest (in 8 h 0 min). Push your work before then.",
        )
        self.assertIn("Session notice (already shown to the user):", self.context(output))

    def test_notice_ends_at_the_runner_deadline_when_earlier(self):
        output = self.run_hook(AGENT_RETIRE_AT=str(NOW + 2 * 3600 + 30 * 60))
        self.assertIn("ends by 8:30 PM PST at the latest (in 2 h 30 min)", output["systemMessage"])

    def test_no_cost_line_until_the_deploy_sets_it(self):
        output = self.run_hook()
        self.assertNotIn("costs", output["systemMessage"])
        output = self.run_hook(AGENT_COST_USD_PER_HOUR="0.38")
        self.assertTrue(
            output["systemMessage"].startswith("⚠ This runtime costs approximately $0.38/hour.\n")
        )

    def test_facts_list_scratch_and_datasets(self):
        (self.datasets / "sentinel-2").mkdir()
        (self.datasets / "3dep").mkdir()
        context = self.context(self.run_hook())
        self.assertIn(f"scratch {self.scratch} (", context)
        self.assertIn("(3dep,sentinel-2, read-only)", context)

    def test_unmounted_outputs_warns_instead_of_failing(self):
        self.outputs.rmdir()
        self.outputs.write_text("not a directory")
        output = self.run_hook()
        self.assertIn("is the outputs bucket mounted?", output["systemMessage"])
        self.assertEqual(self.env_file.read_text(), SOURCE_PROFILE)

    def test_bash_commands_source_the_profile_once(self):
        # A resumed session runs the hook again on the same env file.
        self.run_hook()
        self.run_hook()
        self.assertEqual(self.env_file.read_text().count(SOURCE_PROFILE), 1)

    def test_session_id_is_kept_to_one_path_component(self):
        self.run_hook(CLAUDE_CODE_REMOTE_SESSION_ID="../../etc")
        self.assertTrue((self.outputs / "linux/2026-11-05/.._.._etc").is_dir())


if __name__ == "__main__":
    unittest.main()
