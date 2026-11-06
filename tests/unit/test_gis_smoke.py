"""images/toolsets/gis/smoke.sh against a fake ogrinfo and python3."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable

SCRIPT = REPO / "images/toolsets/gis/smoke.sh"

# python3 answers only the two commands the script runs: the import line and the
# GDAL version check; anything else is refused.
PYTHON = """
case "$2" in
"import osgeo.gdal, rasterio;"*) echo 3.13.3 ;;
"import osgeo.gdal,"*) {imports} ;;
*) echo "python3: unexpected arguments: $*" >&2; exit 2 ;;
esac
"""


class GisSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.work = self.tmp / "work"
        self.work.mkdir()
        write_executable(
            self.tmp / "bin/ogrinfo",
            '[[ "$*" == "-ro -so -al points.geojson" ]] || { echo "ogrinfo: unknown arguments: $*" >&2; exit 2; }\n'
            "echo 'Feature Count: 2'\n",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def smoke(self, imports):
        write_executable(self.tmp / "bin/python3", PYTHON.format(imports=imports))
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=self.work,
            env=base_env(self.tmp, self.tmp / "bin"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_passes_when_the_modules_import_quietly(self):
        result = self.smoke("true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("gis: ogrinfo read 2 features; 16 modules import, on one GDAL 3.13.3", result.stdout)

    def test_a_failed_import_prints_its_traceback_and_fails(self):
        result = self.smoke("echo 'ModuleNotFoundError: No module named pdal' >&2; exit 1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ModuleNotFoundError: No module named pdal", result.stdout)
        self.assertIn("gis: importing the Python modules failed", result.stdout)

    def test_an_import_that_prints_fails(self):
        result = self.smoke("echo 'PDAL: could not load plugin'")
        self.assertEqual(result.returncode, 1)
        self.assertIn("importing the Python modules printed the above", result.stdout)


if __name__ == "__main__":
    unittest.main()
