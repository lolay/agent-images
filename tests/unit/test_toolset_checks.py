"""images/toolsets/toolset-checks.sh: binary checks and smoke tests from a record."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env

SCRIPT = REPO / "images/toolsets/toolset-checks.sh"


class ToolsetChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.record_dir = self.tmp / "toolsets"
        self.record_dir.mkdir()
        self.tmpdir = self.tmp / "scratch-tmp"
        self.out = self.tmp / "out"
        self.out.mkdir()
        (self.record_dir / "toolsets.txt").write_text("go\nbrowser\ngpu\n")

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def smoke_test(self, name, body, header=""):
        (self.record_dir / f"{name}.smoke.sh").write_text(f"#!/bin/bash\n{header}set -euo pipefail\n{body}")

    def run_checks(self, *args, **env):
        settings = {
            "AGENT_TOOLSETS_DIR": str(self.record_dir),
            "TMPDIR": str(self.tmpdir),
            "FAKE_OUT": str(self.out),
        }
        settings.update(env)
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=base_env(self.tmp, self.tmp / "bin", **settings),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_versions_reports_each_check_and_fails_on_any(self):
        (self.record_dir / "go.check").write_text("# a comment\n\ngo: echo 'go1.99'; echo second\n")
        (self.record_dir / "gpu.check").write_text("nvcc: echo 'not found' >&2; exit 1\n")
        result = self.run_checks("versions")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.splitlines(), ["go: go1.99", "nvcc: missing (not found)"])

    def test_smoke_list_is_the_toolsets_with_a_test_in_install_order(self):
        self.smoke_test("gpu", "true\n")
        self.smoke_test("go", "true\n")
        result = self.run_checks("smoke-list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["go", "gpu"])

    def test_live_reads_only_the_header(self):
        self.smoke_test("gpu", "true\n", header="# smoke: live (needs a GPU)\n")
        self.smoke_test("go", "echo '# smoke: live (not a header)'\n" * 12)
        gpu = self.run_checks("live", "gpu")
        self.assertEqual((gpu.returncode, gpu.stdout), (0, "needs a GPU\n"))
        self.assertEqual(self.run_checks("live", "go").returncode, 1)

    def test_smoke_runs_in_a_fresh_temp_directory_it_removes(self):
        self.smoke_test("go", 'pwd >"$FAKE_OUT/pwd"\nls -A >"$FAKE_OUT/ls"\ntouch hello\necho "go: ok"\n')
        result = self.run_checks("smoke", "go")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        work = Path((self.out / "pwd").read_text().strip())
        self.assertEqual(work.parent, self.tmpdir)
        self.assertEqual((self.out / "ls").read_text(), "")
        self.assertFalse(work.exists(), "the temp directory outlived the test")

    def test_smoke_turns_the_toolchains_network_off(self):
        self.smoke_test("go", 'env >"$FAKE_OUT/env"\n')
        self.assertEqual(self.run_checks("smoke", "go").returncode, 0)
        env = dict(line.split("=", 1) for line in (self.out / "env").read_text().splitlines() if "=" in line)
        for key, value in {
            "GOPROXY": "off", "GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "local", "CARGO_NET_OFFLINE": "true",
            "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", "npm_config_offline": "true",
            "CHECKPOINT_DISABLE": "1",
        }.items():
            self.assertEqual(env.get(key), value, key)

    def test_brief_prints_the_last_line_on_a_pass_and_the_tail_on_a_failure(self):
        self.smoke_test("go", "seq 1 9\necho 'go: built and ran hello'\n")
        full = self.run_checks("smoke", "go")
        self.assertEqual(len(full.stdout.splitlines()), 10)
        brief = self.run_checks("smoke", "go", "--brief")
        self.assertEqual(brief.stdout, "go: built and ran hello\n")
        self.smoke_test("go", "seq 1 9\necho 'go: vet failed' >&2\nexit 3\n")
        failed = self.run_checks("smoke", "go", "--brief")
        self.assertEqual(failed.returncode, 3)
        self.assertEqual(failed.stdout.splitlines(), ["6", "7", "8", "9", "go: vet failed"])

    def test_a_hung_test_is_stopped_with_its_children(self):
        # A child that ignores TERM still holds the output pipe; kill-after ends it.
        self.smoke_test("go", "(trap '' TERM; sleep 60) &\nsleep 60\n")
        result = self.run_checks("smoke", "go", "--brief", AGENT_SMOKE_TIMEOUT_SEC="1")
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, "go: timed out after 1s\n")
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_refuses_what_it_cant_run(self):
        self.assertEqual(self.run_checks("smoke", "rust").returncode, 2)
        self.assertEqual(self.run_checks("smoke", "go", "--verbose").returncode, 2)
        self.assertEqual(self.run_checks("bogus").returncode, 2)
        (self.record_dir / "toolsets.txt").unlink()
        result = self.run_checks("smoke-list")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no toolsets recorded", result.stderr)


if __name__ == "__main__":
    unittest.main()
