"""images/cloud/base/apt-prepare.sh: the build's apt bootstrap and the lock's
simulation of it, one sequence (r5.f13). The script runs for real beside the
real apt-build.conf and install-toolset.sh, with fakes for apt-get and
apt-pins.sh; the base Dockerfile and lock.sh are read as text (a build or a
resolve needs Docker and the archive).

The fakes are safe from install-toolset.sh's Homebrew-first PATH (AGENTS.md,
"Test fakes"): apt-prepare.sh only sources its helpers, which change no PATH,
Homebrew ships no apt-get, and apt-pins.sh is run by path, from beside it."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable

SCRIPT = REPO / "images/cloud/base/apt-prepare.sh"
DOCKERFILE = REPO / "images/cloud/base/Dockerfile"
LOCK_SH = REPO / "scripts/cloud/lock.sh"
SNAPSHOT = "20261101T000000Z"
PINS = "openssl=3.0.13-0ubuntu3.6@20260101T000000Z cmake=3.28.3-1build7@20250601T120000Z"
PACKAGES = ["sudo", "ca-certificates", "curl"]

# apt-get as apt-prepare.sh calls it, refusing what the real one would: an
# unknown operation or option, an install that would prompt (no -y), a status
# file that isn't there. An update without the build's apt settings fails too:
# without them, a part of the archive that fails to download would pass. A real
# install records each package in dpkg's status; a simulation prints apt's
# answer and records the packages its status file lists. Each call goes to
# $CALLS, as `apt-get <args>`.
FAKE_APT_GET = r'''
printf 'apt-get %s\n' "$*" >>"$CALLS"
op="${1:-}"
shift || true
simulate=false yes=false status="$FAKE_ROOT/var/lib/dpkg/status" packages=()
while (($# > 0)); do
	case "$1" in
	-y) yes=true ;;
	--no-install-recommends | --allow-downgrades) ;;
	--simulate) simulate=true ;;
	-o)
		[[ "${2:-}" == Dir::State::status=* ]] || { echo "E: fake: option not expected: -o ${2:-}" >&2; exit 100; }
		status="${2#*=}"
		shift
		;;
	-*) echo "E: Command line option '$1' is not understood" >&2; exit 100 ;;
	*) packages+=("$1") ;;
	esac
	shift
done
[[ -f "$FAKE_ROOT/etc/apt/apt.conf.d/40agent-build" ]] || { echo "E: fake: apt ran without apt-build.conf" >&2; exit 100; }
case "$op" in
update)
	((${#packages[@]} == 0)) || { echo "E: The update command takes no arguments" >&2; exit 100; }
	echo "Hit:1 http://archive.ubuntu.com/ubuntu noble InRelease"
	[[ -z "${FAKE_UPDATE_FAIL:-}" ]] || { echo "E: Failed to fetch https://snapshot.ubuntu.com/x  404  Not Found" >&2; exit 100; }
	;;
install)
	((${#packages[@]} > 0)) || { echo "E: fake: nothing to install" >&2; exit 100; }
	[[ -f "$status" ]] || { echo "E: Could not open file $status - open (2: No such file or directory)" >&2; exit 100; }
	if $simulate; then
		printf 'status %s\n' "$(sed -n 's/^Package: //p' "$status" | tr '\n' ' ')" >>"$CALLS"
		echo "NOTE: This is only a simulation!"
		for package in "${packages[@]}"; do
			printf 'Inst %s (1.0 Ubuntu:24.04/noble [amd64])\nConf %s (1.0 Ubuntu:24.04/noble [amd64])\n' "$package" "$package"
		done
	else
		$yes || { echo "Do you want to continue? [Y/n] Abort." >&2; exit 1; }
		printf 'Package: %s\nStatus: install ok installed\n\n' "${packages[@]}" >>"$status"
		echo "Setting up ${packages[*]}"
	fi
	;;
*) echo "E: Invalid operation $op" >&2; exit 100 ;;
esac
'''

# apt-pins.sh's interface: install with well-formed pins, or remove; anything
# else fails, as the real one does. Each call goes to $CALLS, with the
# APT_SNAPSHOT it saw and whether the snapshot setting was there yet.
FAKE_APT_PINS = r'''
set -euo pipefail
conf="$FAKE_ROOT/etc/apt/apt.conf.d/50agent-snapshot"
snapshot="no snapshot"
[[ ! -f "$conf" ]] || snapshot="$(cat "$conf")"
printf 'apt-pins %s (APT_SNAPSHOT=%s; %s)\n' "$*" "${APT_SNAPSHOT:-}" "$snapshot" >>"$CALLS"
case "${1:-}" in
install)
	shift
	for pin in "$@"; do
		[[ "$pin" =~ ^[a-z0-9][a-z0-9.+-]*=[^=@[:space:]]+@[0-9]{8}T[0-9]{6}Z$ ]] ||
			{ echo "apt-pins: '$pin' isn't <name>=<version>@<snapshot>" >&2; exit 1; }
	done
	;;
remove) (($# == 1)) || exit 1 ;;
*) echo "apt-pins: usage" >&2; exit 1 ;;
esac
'''


class AptPrepareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        # The script reads its helpers from beside itself, as in /tmp in the
        # build and the resolve's container.
        self.helpers = self.tmp / "helpers"
        self.helpers.mkdir()
        shutil.copy(SCRIPT, self.helpers / "apt-prepare.sh")
        shutil.copy(REPO / "images/cloud/base/apt-build.conf", self.helpers / "apt-build.conf")
        shutil.copy(REPO / "images/toolsets/install-toolset.sh", self.helpers / "install-toolset.sh")
        (self.helpers / "apt-pins.sh").write_text(FAKE_APT_PINS)
        self.root = self.tmp / "root"
        self.bin = self.tmp / "bin"
        write_executable(self.bin / "apt-get", FAKE_APT_GET)
        self.calls = self.tmp / "calls"

    def run_prepare(self, *args, **env):
        # Each run in a fresh stock image: dpkg's status, and no apt settings.
        shutil.rmtree(self.root, ignore_errors=True)
        (self.root / "var/lib/dpkg").mkdir(parents=True)
        (self.root / "var/lib/dpkg/status").write_text("Package: bash\nStatus: install ok installed\n\n")
        self.calls.unlink(missing_ok=True)
        return subprocess.run(
            ["bash", str(self.helpers / "apt-prepare.sh"), *args],
            env=base_env(self.tmp, self.bin, APT_PREPARE_ROOT=str(self.root), FAKE_ROOT=str(self.root),
                         CALLS=str(self.calls), **env),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )

    def call_log(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def build(self, **env):
        result = self.run_prepare(*PACKAGES, **{"APT_SNAPSHOT": SNAPSHOT, "APT_PINS": PINS, **env})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def simulate(self, **env):
        result = self.run_prepare("--simulate", *PACKAGES, **{"APT_PINS": PINS, **env})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    OPTIONS = "-y --no-install-recommends --allow-downgrades"

    def test_the_build_runs_the_sequence(self):
        self.build()
        self.assertEqual(self.call_log(), [
            "apt-get update",
            f"apt-get install {self.OPTIONS} ca-certificates",
            f'apt-pins install {PINS} (APT_SNAPSHOT={SNAPSHOT}; APT::Snapshot "{SNAPSHOT}";)',
            "apt-get update",
            f"apt-get install {self.OPTIONS} sudo ca-certificates curl",
        ])
        conf = self.root / "etc/apt/apt.conf.d"
        self.assertEqual((conf / "40agent-build").read_text(), (REPO / "images/cloud/base/apt-build.conf").read_text())
        self.assertEqual((conf / "50agent-snapshot").read_text(), f'APT::Snapshot "{SNAPSHOT}";\n')

    def test_the_simulation_runs_the_builds_sequence_with_its_flags(self):
        # The lock is right only while it resolves what the build installs: the
        # same steps, and the same options on every install, the simulation's
        # included. What differs is only the snapshot (the resolve picks it) and
        # the simulation itself.
        self.build()
        build = self.call_log()
        result = self.simulate()
        simulated = [line for line in self.call_log() if not line.startswith("status ")]
        self.assertEqual(len(simulated), len(build), simulated)
        self.assertRegex(simulated[-1], r" --simulate -o Dir::State::status=\S+ ")
        normalized = [re.sub(r" --simulate -o Dir::State::status=\S+", "", line) for line in simulated]
        expected = [line.replace(f"APT_SNAPSHOT={SNAPSHOT}; APT::Snapshot \"{SNAPSHOT}\";",
                                 "APT_SNAPSHOT=; no snapshot") for line in build]
        self.assertEqual(normalized, expected)
        self.assertFalse((self.root / "etc/apt/apt.conf.d/50agent-snapshot").exists())
        self.assertTrue((self.root / "etc/apt/apt.conf.d/40agent-build").exists())
        # stdout is apt's answer alone, the earlier steps' output kept back.
        self.assertIn("Inst sudo (1.0 Ubuntu:24.04/noble [amd64])\n", result.stdout)
        self.assertNotIn("Hit:1", result.stdout)
        self.assertNotIn("Setting up", result.stdout)

    def test_the_simulation_reads_the_package_state_before_ca_certificates(self):
        # The build installs ca-certificates (and its openssl) from today's
        # archive before the snapshot; the lock still lists them as the build
        # installs them, so the simulation starts from the stock image's state.
        self.simulate()
        log = self.call_log()
        self.assertIn("status bash ", log)
        self.assertIn("ca-certificates", (self.root / "var/lib/dpkg/status").read_text(), "the step ran")

    def test_no_pins_still_runs_every_step(self):
        # ca-certificates whether or not there are pins, in both modes.
        for mode in ("build", "simulate"):
            with self.subTest(mode):
                getattr(self, mode)(APT_PINS="")
                log = self.call_log()
                self.assertIn(f"apt-get install {self.OPTIONS} ca-certificates", log)
                self.assertTrue([line for line in log if line.startswith("apt-pins install (")], log)

    def test_the_build_needs_a_snapshot_and_the_simulation_refuses_one(self):
        result = self.run_prepare(*PACKAGES, APT_PINS=PINS)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no APT_SNAPSHOT", result.stderr)
        self.assertEqual(self.call_log(), [])
        result = self.run_prepare("--simulate", *PACKAGES, APT_SNAPSHOT=SNAPSHOT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("APT_SNAPSHOT is the build's", result.stderr)
        self.assertEqual(self.call_log(), [])

    def test_a_failed_step_fails_with_its_output(self):
        for mode in ("build", "simulate"):
            with self.subTest(mode):
                args = ("--simulate",) if mode == "simulate" else ()
                env = {} if mode == "simulate" else {"APT_SNAPSHOT": SNAPSHOT}
                result = self.run_prepare(*args, *PACKAGES, FAKE_UPDATE_FAIL="1", **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Inst ", result.stdout)
                self.assertIn("404  Not Found", result.stderr)
                self.assertEqual(self.call_log(), ["apt-get update"])

    def test_usage(self):
        for args in ([], ["--simulate"], ["--nope", "sudo"]):
            with self.subTest(args):
                result = self.run_prepare(*args, APT_SNAPSHOT=SNAPSHOT)
                self.assertEqual(result.returncode, 1)
                self.assertIn("usage: apt-prepare.sh", result.stderr)
                self.assertEqual(self.call_log(), [])


def code_lines(path: Path):
    """A file's lines, comments left out (a Dockerfile's and a script's)."""
    return [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]


class OneSequenceTests(unittest.TestCase):
    """The base Dockerfile and lock.sh's resolve both run apt-prepare.sh, with
    every file it reads beside it, and neither, nor any other type's Dockerfile
    or cloud script, restates its steps (r5.f13)."""

    def helpers(self):
        """What apt-prepare.sh reads from beside itself."""
        names = set(re.findall(r'"\$here/([^"]+)"', SCRIPT.read_text()))
        self.assertEqual(names, {"apt-build.conf", "apt-pins.sh", "install-toolset.sh"})
        return names | {"apt-prepare.sh"}

    def source_of(self, name):
        return f"images/toolsets/{name}" if name == "install-toolset.sh" else f"images/cloud/base/{name}"

    def test_the_base_runs_it_with_its_helpers(self):
        text = DOCKERFILE.read_text()
        for name in self.helpers():
            self.assertIn(f"--mount=type=bind,source={self.source_of(name)},target=/tmp/{name} ", text)
        self.assertRegex(text, r"\n\tbash /tmp/apt-prepare\.sh \$\(sed [^\n]*/tmp/base-packages\.txt\)")

    def test_the_resolve_runs_it_with_its_helpers(self):
        text = LOCK_SH.read_text()
        for name in self.helpers():
            self.assertIn(f'--mount "type=bind,source=$PWD/{self.source_of(name)},target=/tmp/{name},readonly"', text)
        self.assertIn('"$base" /tmp/apt-prepare.sh --simulate "${packages[@]}"', text)

    # A step of the sequence, written anywhere but apt-prepare.sh.
    STEPS = re.compile(r"\bapt-get\b.*\b(update|install)\b|apt-pins\.sh install|APT::Snapshot|APT_CONFIG=|"
                       r"apt\.conf\.d/40agent-build|apt\.conf\.d/50agent-snapshot")

    def test_nothing_else_restates_it(self):
        # Every runner type's Dockerfile and every cloud script. The
        # orchestrator isn't built from the base: no snapshot, no pins, its own
        # apt-get. The runner stage removes the settings the sequence leaves
        # (rm -f), which isn't restating them.
        files = [p for p in sorted((REPO / "images/cloud").glob("*/Dockerfile")) if p.parent.name != "orchestrator"]
        files += sorted((REPO / "scripts/cloud").glob("*.sh"))
        self.assertIn(DOCKERFILE, files)
        self.assertIn(LOCK_SH, files)
        restated = [
            f"{path.relative_to(REPO)}: {line.strip()}"
            for path in files
            for line in code_lines(path)
            if self.STEPS.search(line) and not line.strip().startswith("sudo rm -f /etc/apt/apt.conf.d/")
        ]
        self.assertEqual(restated, [])


if __name__ == "__main__":
    unittest.main()
