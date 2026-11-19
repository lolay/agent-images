"""install-toolset.sh's apt_update: the one `apt-get update` of the cloud builds,
run again while a repository's index is out of sync (NVIDIA's CUDA repo served
two versions of Packages.gz for about an hour on 2026-11-18, and builds failed
at different steps with "File has unexpected size ... Mirror sync in progress?").

It runs for real, sourced from a copy of install-toolset.sh (AGENTS.md, "Test
fakes": the fake bin stays in front because sourcing changes no PATH), with fakes
for sudo, apt-get and sleep. The Dockerfiles that call it are read as text (a
build needs Docker and the archive)."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable

SCRIPT = REPO / "images/toolsets/install-toolset.sh"

# NVIDIA's failure of 2026-11-18, as apt printed it.
CUDA_SYNC = (
    "E: Failed to fetch https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/Packages.gz"
    "  File has unexpected size (2088182 != 2089045). Mirror sync in progress?"
)
# The other messages the retry covers, and what a failure the retry must not hide
# looks like.
HASH_MISMATCH = (
    "E: Failed to fetch http://archive.ubuntu.com/ubuntu/dists/noble-updates/main/binary-amd64/by-hash/SHA256/ab12"
    "  Hash Sum mismatch\n   Hashes of expected file:\n    - SHA256:ab12"
)
SIZE_ONLY = (
    "E: Failed to fetch https://example.com/Packages.gz  File has unexpected size (1 != 2)"
)
NOT_FOUND = "E: Failed to fetch https://snapshot.ubuntu.com/ubuntu/x/dists/noble/InRelease  404  Not Found [IP: 91.189.91.81 443]"
UNREACHABLE = "E: Failed to fetch http://archive.ubuntu.com/ubuntu/dists/noble/InRelease  Temporary failure resolving 'archive.ubuntu.com'"
TRAILER = "E: Some index files failed to download. They have been ignored, or old ones used instead."

# apt-get as the helpers call it: -q first, a known operation, no arguments to
# update, as the real one refuses. The first FAILURES updates fail the way apt
# does (the error and the trailer on stderr, exit 100), with FAKE_ERROR; each
# call goes to $CALLS.
FAKE_APT_GET = r'''
[[ "${1:-}" == -q ]] || { echo "E: fake: expected -q first" >&2; exit 100; }
shift
op="${1:-}"
shift || true
case "$op" in
update)
	(($# == 0)) || { echo "E: The update command takes no arguments" >&2; exit 100; }
	n=$(($(cat "$STATE" 2>/dev/null || echo 0) + 1))
	echo "$n" >"$STATE"
	echo "apt-get update" >>"$CALLS"
	echo "Hit:1 http://archive.ubuntu.com/ubuntu noble InRelease"
	if ((n <= ${FAILURES:-0})); then
		printf '%s\n' "$FAKE_ERROR" "E: Some index files failed to download. They have been ignored, or old ones used instead." >&2
		exit 100
	fi
	;;
*) echo "E: Invalid operation $op" >&2; exit 100 ;;
esac
'''

# sleep without waiting, refusing what the real one does (no interval, a word).
FAKE_SLEEP = r'''
(($# == 1)) && [[ "$1" =~ ^[0-9]+([.][0-9]+)?[smhd]?$ ]] || { echo "sleep: invalid time interval '$*'" >&2; exit 1; }
echo "sleep $1" >>"$CALLS"
'''

# sudo as the build's user has it, with no root here: it runs its command, and
# refuses what real sudo would (no command).
FAKE_SUDO = '(($# > 0)) || { echo "usage: sudo command" >&2; exit 1; }\nexec "$@"\n'


class AptUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.toolsets = self.tmp / "toolsets"
        self.toolsets.mkdir()
        shutil.copy(SCRIPT, self.toolsets / "install-toolset.sh")
        self.bin = self.tmp / "bin"
        write_executable(self.bin / "sudo", FAKE_SUDO)
        write_executable(self.bin / "apt-get", FAKE_APT_GET)
        write_executable(self.bin / "sleep", FAKE_SLEEP)
        self.calls = self.tmp / "calls"
        self.scratch = self.tmp / "scratch"  # TMPDIR: where mktemp puts apt_update's files
        self.scratch.mkdir()
        self.probe = self.tmp / "probe.sh"
        self.probe.write_text(f'TOOLSET_LOG_NAME=probe\nsource "{self.toolsets}/install-toolset.sh"\napt_update\necho "after"\n')

    def run_update(self, error=CUDA_SYNC, failures=0):
        return subprocess.run(
            ["bash", str(self.probe)],
            env=base_env(self.tmp, self.bin, CALLS=str(self.calls), STATE=str(self.tmp / "state"),
                         FAILURES=str(failures), FAKE_ERROR=error, TMPDIR=str(self.scratch)),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )

    def call_log(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def updates(self):
        return [line for line in self.call_log() if line == "apt-get update"]

    def sleeps(self):
        return [line for line in self.call_log() if line.startswith("sleep ")]

    def test_a_clean_update_runs_once_and_waits_for_nothing(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.call_log(), ["apt-get update"])
        self.assertIn("Hit:1 http://archive.ubuntu.com", result.stdout)
        self.assertIn("after", result.stdout)

    def test_the_cuda_repos_unexpected_size_is_retried_until_it_clears(self):
        result = self.run_update(failures=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.call_log(), [
            "apt-get update", "sleep 15", "apt-get update", "sleep 45", "apt-get update"])
        self.assertIn("after", result.stdout)

    def test_each_failed_attempt_is_logged_with_apts_error(self):
        result = self.run_update(failures=2)
        self.assertIn(f"==> probe: apt_update: attempt 1 of 4 failed: {CUDA_SYNC}\n", result.stdout)
        self.assertIn(f"==> probe: apt_update: attempt 2 of 4 failed: {CUDA_SYNC}\n", result.stdout)
        self.assertIn("==> probe: apt_update: retrying in 15s\n", result.stdout)
        self.assertIn("==> probe: apt_update: retrying in 45s\n", result.stdout)
        # apt's own output still reaches the log, both streams.
        self.assertEqual(result.stdout.count("Hit:1 http://archive.ubuntu.com"), 3)
        self.assertEqual(result.stderr.count(TRAILER), 2)

    def test_the_last_attempt_can_be_the_one_that_works(self):
        result = self.run_update(failures=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.updates()), 4)
        self.assertEqual(self.sleeps(), ["sleep 15", "sleep 45", "sleep 90"])

    def test_it_gives_up_after_the_fourth_attempt_with_apts_status(self):
        result = self.run_update(failures=99)
        self.assertEqual(result.returncode, 100)
        self.assertNotIn("after", result.stdout)
        self.assertEqual(len(self.updates()), 4)
        self.assertEqual(self.sleeps(), ["sleep 15", "sleep 45", "sleep 90"])
        self.assertIn("==> probe: apt_update: attempt 4 of 4 failed: " + CUDA_SYNC, result.stdout)
        self.assertIn("==> probe: apt_update: failed 4 times in a row, with the archive out of sync\n", result.stdout)
        self.assertEqual(result.stdout.count("apt_update: retrying"), 3)
        self.assertIn(TRAILER, result.stderr)

    def test_any_other_error_fails_at_once(self):
        for error in (NOT_FOUND, UNREACHABLE):
            with self.subTest(error):
                self.calls.unlink(missing_ok=True)
                (self.tmp / "state").unlink(missing_ok=True)
                result = self.run_update(error=error, failures=99)
                self.assertEqual(result.returncode, 100)
                self.assertEqual(self.call_log(), ["apt-get update"])
                self.assertIn(error, result.stderr)
                self.assertNotIn("retrying", result.stdout)
                self.assertNotIn("after", result.stdout)

    def test_the_other_messages_of_an_index_out_of_sync_are_retried_too(self):
        for error in (HASH_MISMATCH, SIZE_ONLY):
            with self.subTest(error):
                self.calls.unlink(missing_ok=True)
                (self.tmp / "state").unlink(missing_ok=True)
                result = self.run_update(error=error, failures=1)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.call_log(), ["apt-get update", "sleep 15", "apt-get update"])

    def test_a_sync_error_on_an_earlier_attempt_doesnt_retry_a_later_failure(self):
        # The retry reads this attempt's output, not an earlier one's: a sync
        # error on the first attempt and a 404 on the second stops there.
        write_executable(self.bin / "apt-get", FAKE_APT_GET.replace(
            "printf '%s\\n' \"$FAKE_ERROR\"", "printf '%s\\n' \"$( ((n == 1)) && echo \"$FAKE_ERROR\" || echo \"$FAKE_ERROR_AFTER\")\""))
        result = subprocess.run(
            ["bash", str(self.probe)],
            env=base_env(self.tmp, self.bin, CALLS=str(self.calls), STATE=str(self.tmp / "state"),
                         FAILURES="99", FAKE_ERROR=CUDA_SYNC, FAKE_ERROR_AFTER=NOT_FOUND),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 100)
        self.assertEqual(self.call_log(), ["apt-get update", "sleep 15", "apt-get update"])
        self.assertIn(NOT_FOUND, result.stderr)

    def test_its_temp_files_are_removed(self):
        for failures in (0, 1, 99):
            with self.subTest(failures=failures):
                (self.tmp / "state").unlink(missing_ok=True)
                self.run_update(failures=failures)
                self.assertEqual(list(self.scratch.iterdir()), [])
        # And on the way out for an error it doesn't retry.
        self.run_update(error=NOT_FOUND, failures=1)
        self.assertEqual(list(self.scratch.iterdir()), [])


class InstallPathTests(unittest.TestCase):
    """A toolset's packages go through apt_update: a sync error on its update
    doesn't fail its layer (the jvm and colmap steps of #142)."""

    def test_a_toolsets_package_install_rides_out_a_sync_error(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        toolsets = tmp / "toolsets"
        (toolsets / "jvm").mkdir(parents=True)
        shutil.copy(SCRIPT, toolsets / "install-toolset.sh")
        (toolsets / "jvm/packages.txt").write_text("# the JDK\nopenjdk-21-jdk\n")
        bin_dir = tmp / "bin"
        write_executable(bin_dir / "sudo", FAKE_SUDO)
        write_executable(bin_dir / "sleep", FAKE_SLEEP)
        # The install and clean the toolset's list goes on to, logged as apt got them.
        install = "install | clean) echo \"${op}${*:+ $*}\" >>\"$CALLS\" ;;\n"
        write_executable(bin_dir / "apt-get", FAKE_APT_GET.replace(
            '*) echo "E: Invalid operation', install + '*) echo "E: Invalid operation'))
        calls = tmp / "calls"
        result = subprocess.run(
            ["bash", str(toolsets / "install-toolset.sh"), "jvm"],
            env=base_env(tmp, bin_dir, CALLS=str(calls), STATE=str(tmp / "state"), FAILURES="2",
                         FAKE_ERROR=CUDA_SYNC, AGENT_TOOLSETS_DIR=str(tmp / "record")),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.read_text().splitlines(), [
            "apt-get update", "sleep 15", "apt-get update", "sleep 45", "apt-get update",
            "install -y --no-install-recommends --allow-downgrades openjdk-21-jdk", "clean",
        ])
        self.assertIn("==> install-toolset: apt_update: retrying in 45s\n", result.stdout)


def code(path: Path) -> str:
    """A file's text with comments dropped and continued lines joined, so an
    `apt-get \\` newline `update` is one line."""
    lines = [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]
    return re.sub(r"\\\n\s*", " ", "\n".join(lines))


class OneUpdateTests(unittest.TestCase):
    """The retry is the one source: no script or Dockerfile of a cloud or VM image
    build runs `apt-get update` itself, and each place that needs one calls
    apt_update."""

    UPDATE = re.compile(r"\bapt(-get)?\b[^\n|;&]*\bupdate\b")

    def build_files(self):
        files = sorted(
            path
            for pattern in ("images/**/*.sh", "images/**/Dockerfile*", "images/**/*.pkr.hcl", "scripts/cloud/*.sh")
            for path in REPO.glob(pattern)
        )
        self.assertIn(SCRIPT, files)
        self.assertIn(REPO / "images/cloud/orchestrator/Dockerfile", files)
        self.assertIn(REPO / "images/cloud/base/Dockerfile", files)
        self.assertIn(REPO / "scripts/cloud/lock.sh", files)
        return files

    def test_nothing_but_apt_update_runs_an_update(self):
        updates = [
            f"{path.relative_to(REPO)}: {line.strip()}"
            for path in self.build_files()
            for line in code(path).splitlines()
            if self.UPDATE.search(line)
        ]
        # install-toolset.sh's, in apt_update, is the one.
        self.assertEqual(updates, ["images/toolsets/install-toolset.sh: apt update >\"$out\" 2>\"$err\" || status=$?"])

    def test_the_places_that_update_call_apt_update(self):
        calls = {
            path.relative_to(REPO).as_posix()
            for path in self.build_files()
            if re.search(r"(^|[\s'(])apt_update\b", code(path))
        }
        self.assertEqual(calls, {
            "images/cloud/base/apt-prepare.sh",       # the base's bootstrap and the lock's simulation of it
            "images/cloud/orchestrator/Dockerfile",   # its own build, on plain Ubuntu
            "images/toolsets/install-toolset.sh",     # install_apt_packages: every toolset, build-colmap.sh
        })
        self.assertEqual(code(REPO / "images/cloud/base/apt-prepare.sh").count("step apt_update"), 2)

    def test_the_orchestrator_has_it_mounted_to_source(self):
        text = code(REPO / "images/cloud/orchestrator/Dockerfile")
        self.assertIn("source=images/toolsets/install-toolset.sh,target=/tmp/install-toolset.sh", text)
        self.assertIn("bash -c 'source /tmp/install-toolset.sh && apt_update'", text)

    def test_apt_update_is_exported_to_a_toolsets_install_sh(self):
        # install_apt_packages, which an install.sh calls, calls it.
        self.assertRegex(code(SCRIPT), r"export -f [^\n]*\bapt_update\b")


if __name__ == "__main__":
    unittest.main()
