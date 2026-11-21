"""The Android image's staged layout: one list (images/android/staging.txt),
assembled by images/android/stage.sh, which every build stages through (r1.f31)."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO

STAGE = REPO / "images/android/stage.sh"
LIST = REPO / "images/android/staging.txt"
# Every driver that stages the Android image, and how each names the stager.
DRIVERS = {
    "scripts/linux/build-image.sh": '"$AGENT_IMAGES_DIR/images/android/stage.sh" "$host_staging"',
    "images/android/gce/build.pkr.hcl": "'${path.root}/../stage.sh' '${local.host_staging}' gce",
    ".github/workflows/android-image-smoke.yml": 'images/android/stage.sh "$STAGING_DIR"',
}


def list_sources():
    """The repo paths the list stages."""
    sources = []
    for line in LIST.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            sources.append(line.split()[0].rstrip("/"))
    return sources


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=True)

    def stage(self, *args, dest=None):
        dest = dest or self.tmp / "staging"
        result = subprocess.run(["bash", str(STAGE), str(dest), *args],
                                capture_output=True, text=True, timeout=60)
        return result, dest

    def files(self, dest):
        return sorted(str(path.relative_to(dest)) for path in dest.rglob("*") if path.is_file())

    def test_the_layout_the_provisioners_read(self):
        result, dest = self.stage()
        self.assertEqual(result.returncode, 0, result.stderr)
        files = self.files(dest)
        for path in ("scripts/setup-user.sh", "scripts/install-packages.sh", "toolsets.txt",
                     "agent-runner.sh", "runners/claude.sh", "claude/settings.json",
                     "agent-emulator.sh", "agent-runner.service", "toolsets/install-toolset.sh",
                     "toolsets/android/install.sh"):
            self.assertIn(path, files)
        self.assertFalse([path for path in files if path.startswith("gce/")])

    def test_the_gce_variant_adds_its_guest_pieces(self):
        result, dest = self.stage("gce")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gce/agent-gce-order.sh", self.files(dest))
        self.assertIn("scripts/setup-user.sh", self.files(dest))

    def test_an_unknown_variant_is_refused(self):
        # A misspelled variant would stage the common layout alone.
        result, _ = self.stage("gcee")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no lines for variant gcee", result.stderr)

    def test_a_non_empty_destination_is_refused(self):
        dest = self.tmp / "staging"
        dest.mkdir()
        (dest / "stale").write_text("x")
        result, _ = self.stage(dest=dest)
        self.assertEqual(result.returncode, 1)
        self.assertIn("isn't empty", result.stderr)


class OneListTests(unittest.TestCase):
    """Every driver stages through stage.sh and names none of the list's sources
    itself: a second copy of the layout is what r1.f31 found three of."""

    def test_every_driver_stages_through_the_one_list(self):
        self.assertIn("images/android/staging.txt", STAGE.read_text())
        sources = list_sources()
        self.assertIn("images/shared", sources)
        for driver, call in DRIVERS.items():
            with self.subTest(driver=driver):
                text = (REPO / driver).read_text()
                self.assertIn(call, text)
                code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
                for source in sources:
                    # The driver's own spellings of a source: from the repo root
                    # ($AGENT_IMAGES_DIR/images/shared), or from its own directory
                    # (the GCE template's ${path.root}/../../shared).
                    relative = os.path.relpath(REPO / source, (REPO / driver).parent)
                    for spelling in (source, relative):
                        self.assertIsNone(
                            re.search(rf"(^|[\s\"'/{{}}]){re.escape(spelling)}([/\s\"'.*]|$)", code, re.M),
                            f"{driver} stages {spelling} itself")
                # Or a root to build them from ($android_dir/files).
                self.assertIsNone(re.search(r"images/android(?!/stage\.sh)([\s\"']|$)", code, re.M),
                                  f"{driver} names images/android itself")

if __name__ == "__main__":
    unittest.main()
