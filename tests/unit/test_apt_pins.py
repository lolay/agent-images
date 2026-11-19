"""images/cloud/base/apt-pins.sh: a pin's own Ubuntu snapshot as an extra apt
source, with preferences for the pinned packages alone (specs/gcp.md, "Pinning a
dependency"), and how the cloud builds use it. The script runs for real against
a scratch root; the Dockerfiles are read as text (a build needs the archive)."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO

SCRIPT = REPO / "images/cloud/base/apt-pins.sh"
SOURCES = "etc/apt/sources.list.d/agent-pins.sources"
PREFERENCES = "etc/apt/preferences.d/agent-pins"


class AptPinsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "etc").mkdir()
        (self.root / "etc/os-release").write_text('NAME="Ubuntu"\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n')

    def run_pins(self, *args, **env):
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30,
                              env={"PATH": "/usr/bin:/bin", "APT_PINS_ROOT": str(self.root), **env})

    def read(self, name):
        return (self.root / name).read_text()

    def test_install_adds_each_snapshot_once_and_prefers_each_pinned_version(self):
        result = self.run_pins("install", "openssl=3.0.13-0ubuntu3.6@20260101T000000Z",
                               "libssl3t64=3.0.13-0ubuntu3.6@20260101T000000Z",
                               "cmake=3.28.3-1build7@20250601T120000Z")
        self.assertEqual(result.returncode, 0, result.stderr)
        stanzas = [s for s in self.read(SOURCES).split("\n\n") if s.strip()]
        self.assertEqual(len(stanzas), 2)
        self.assertEqual(stanzas[0].splitlines(), [
            "Types: deb",
            "URIs: https://snapshot.ubuntu.com/ubuntu/20260101T000000Z/",
            "Suites: noble noble-updates noble-security",
            "Components: main restricted universe multiverse",
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg",
            # Without it, the build's APT::Snapshot would move this source to the
            # type's snapshot, through the Snapshots field of its Release file.
            "Snapshot: no",
        ])
        self.assertIn("URIs: https://snapshot.ubuntu.com/ubuntu/20250601T120000Z/", stanzas[1])
        self.assertEqual(self.read(PREFERENCES), (
            "Package: openssl\nPin: version 3.0.13-0ubuntu3.6\nPin-Priority: 1001\n\n"
            "Package: libssl3t64\nPin: version 3.0.13-0ubuntu3.6\nPin-Priority: 1001\n\n"
            "Package: cmake\nPin: version 3.28.3-1build7\nPin-Priority: 1001\n\n"
        ))
        self.assertNotIn("Package: *", self.read(PREFERENCES), "only the pinned packages")

    def test_no_pins_writes_nothing(self):
        result = self.run_pins("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / SOURCES).exists())
        self.assertFalse((self.root / PREFERENCES).exists())

    def test_a_malformed_pin_writes_nothing(self):
        for pin in ("openssl@20260101T000000Z", "openssl=3.0.13", "openssl=3.0.13@january",
                    "OpenSSL=3.0.13@20260101T000000Z", "openssl=3.0.13@20260101T000000Z@x"):
            with self.subTest(pin):
                result = self.run_pins("install", "cmake=3.28.3-1build7@20250601T120000Z", pin)
                self.assertEqual(result.returncode, 1)
                self.assertIn("isn't <name>=<version>@<snapshot>", result.stderr)
                self.assertFalse((self.root / SOURCES).exists())
                self.assertFalse((self.root / PREFERENCES).exists())

    def test_a_pin_snapshot_newer_than_the_builds_is_refused(self):
        # Its whole index is a source: newer versions of every other package.
        pins = ("cmake=3.28.3-1build7@20250601T120000Z", "openssl=1@20260101T000000Z")
        result = self.run_pins("install", *pins, APT_SNAPSHOT="20251231T235959Z")
        self.assertEqual(result.returncode, 1)
        self.assertIn("20260101T000000Z is newer than the build's snapshot, 20251231T235959Z", result.stderr)
        self.assertFalse((self.root / SOURCES).exists())
        for snapshot in ("20260101T000000Z", "20261101T000000Z"):
            with self.subTest(snapshot):
                self.assertEqual(self.run_pins("install", *pins, APT_SNAPSHOT=snapshot).returncode, 0)

    def test_a_package_pinned_twice_is_refused(self):
        result = self.run_pins("install", "openssl=1@20260101T000000Z", "openssl=2@20250101T000000Z")
        self.assertEqual(result.returncode, 1)
        self.assertIn("openssl is pinned twice", result.stderr)
        self.assertFalse((self.root / PREFERENCES).exists())

    def test_remove_deletes_both(self):
        self.assertEqual(self.run_pins("install", "openssl=1@20260101T000000Z").returncode, 0)
        result = self.run_pins("remove")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / SOURCES).exists())
        self.assertFalse((self.root / PREFERENCES).exists())

    def test_usage(self):
        for args in ([], ["nope"], ["remove", "extra"]):
            with self.subTest(args):
                self.assertEqual(self.run_pins(*args).returncode, 1)


class BuildUsesPinsTests(unittest.TestCase):
    """The base sets the pins up (through apt-prepare.sh, whose order
    test_apt_prepare checks), the runner stage removes them, and every apt
    install of a package list in a cloud build allows the downgrade a pin from
    an older snapshot can need."""

    def text(self, path):
        return (REPO / path).read_text()

    def test_the_base_takes_them_and_mounts_the_script(self):
        base = self.text("images/cloud/base/Dockerfile")
        self.assertIn("source=images/cloud/base/apt-pins.sh,target=/tmp/apt-pins.sh", base)
        self.assertRegex(base, r"(?m)^ARG APT_PINS$")
        self.assertIn('read -ra pins <<<"${APT_PINS:-}"', self.text("images/cloud/base/apt-prepare.sh"))

    def test_the_runner_stage_removes_them(self):
        runner = self.text("images/cloud/runner/Dockerfile")
        self.assertIn("source=images/cloud/base/apt-pins.sh,target=/tmp/images/apt-pins.sh", runner)
        self.assertIn("sudo bash /tmp/images/apt-pins.sh remove\n", runner)

    def test_every_package_list_install_allows_a_pins_downgrade(self):
        # Every cloud Dockerfile but the orchestrator's (no snapshot, no pins),
        # every script beside the base's, and every script under
        # images/toolsets: each install takes apt_install_options, the one
        # place the options are written.
        files = [p for p in (REPO / "images/cloud").glob("*/Dockerfile") if p.parent.name != "orchestrator"]
        files += list((REPO / "images/cloud/base").glob("*.sh"))
        files += list((REPO / "images/toolsets").rglob("*.sh"))
        installs, options = [], []
        for path in files:
            text = path.read_text()
            for number, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if "--allow-downgrades" in line:
                    options.append(f"{path.relative_to(REPO)}:{number}")
                if not re.search(r"\bapt(-get)? (-q )?install\b", line):
                    continue
                installs.append(f"{path.relative_to(REPO)}:{number}")
                with self.subTest(f"{path.relative_to(REPO)}:{number}"):
                    self.assertIn('install "${options[@]}" ', line)
                    self.assertIn("mapfile -t options < <(apt_install_options)", text)
        # Not vacuous: install-toolset.sh's helper, which every toolset script
        # uses rather than its own apt install (r4.f13), and apt-prepare.sh's
        # (the base's, and the lock's simulation).
        self.assertEqual({install.split(":")[0] for install in installs},
                         {"images/cloud/base/apt-prepare.sh", "images/toolsets/install-toolset.sh"}, installs)
        self.assertEqual(options, ["images/toolsets/install-toolset.sh:" + str(next(
            n for n, line in enumerate(self.text("images/toolsets/install-toolset.sh").splitlines(), 1)
            if line.startswith("apt_install_options()")))])
        self.assertIn("printf '%s\\n' -y --no-install-recommends --allow-downgrades;",
                      self.text("images/toolsets/install-toolset.sh"))


if __name__ == "__main__":
    unittest.main()
