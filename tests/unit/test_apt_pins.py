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

    def run_pins(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30,
                              env={"PATH": "/usr/bin:/bin", "APT_PINS_ROOT": str(self.root)})

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
    """The base sets the pins up before the snapshot's first update, the runner
    stage removes them, and every apt install of a package list in a cloud
    build allows the downgrade a pin from an older snapshot can need."""

    def text(self, path):
        return (REPO / path).read_text()

    def test_the_base_installs_them_before_the_snapshots_update(self):
        base = self.text("images/cloud/base/Dockerfile")
        self.assertIn("source=images/cloud/base/apt-pins.sh,target=/tmp/apt-pins.sh", base)
        self.assertRegex(base, r"(?m)^ARG APT_PINS$")
        pins = base.find("bash /tmp/apt-pins.sh install $APT_PINS")
        self.assertNotEqual(pins, -1)
        self.assertLess(base.index("/etc/apt/apt.conf.d/50agent-snapshot"), pins)
        self.assertNotEqual(base.find("apt-get update", pins), -1, "no update after the pins")

    def test_the_runner_stage_removes_them(self):
        runner = self.text("images/cloud/runner/Dockerfile")
        self.assertIn("source=images/cloud/base/apt-pins.sh,target=/tmp/images/apt-pins.sh", runner)
        self.assertIn("sudo bash /tmp/images/apt-pins.sh remove\n", runner)

    def test_every_package_list_install_allows_a_pins_downgrade(self):
        # Every cloud Dockerfile but the orchestrator's (no snapshot, no pins)
        # and every script under images/toolsets. Exempt: the base's
        # ca-certificates, before any pin, and a local .deb.
        files = [p for p in (REPO / "images/cloud").glob("*/Dockerfile") if p.parent.name != "orchestrator"]
        files += list((REPO / "images/toolsets").rglob("*.sh"))
        installs = []
        for path in files:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if line.lstrip().startswith("#") or not re.search(r"\bapt(-get)? (-q )?install -", line):
                    continue
                if line.rstrip(" \\").endswith((" ca-certificates", '.deb"')):
                    continue
                installs.append(f"{path.relative_to(REPO)}:{number}")
                with self.subTest(f"{path.relative_to(REPO)}:{number}"):
                    self.assertIn("--allow-downgrades", line)
        self.assertGreaterEqual(len(installs), 4, installs)


if __name__ == "__main__":
    unittest.main()
