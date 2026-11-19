"""images/toolsets/install-toolset.sh: what `record` leaves in the record directory."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable
from test_lock import FAKE_CURL, executable

SCRIPT = REPO / "images/toolsets/install-toolset.sh"

FILES = {
    "CLAUDE.md": "md",
    "check.txt": "check",
    "smoke.sh": "smoke.sh",
    "profile.sh": "profile.sh",
    "path.txt": "path",
}


class InstallToolsetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # The script finds its toolsets next to itself, so run a copy beside fake ones.
        self.toolsets = self.tmp / "toolsets"
        self.toolsets.mkdir()
        self.script = self.toolsets / "install-toolset.sh"
        shutil.copy(SCRIPT, self.script)
        self.record_dir = self.tmp / "record"
        self.bin = self.tmp / "bin"
        # No root here: sudo runs its command as the current user, and refuses
        # what real sudo would (no command).
        write_executable(
            self.bin / "sudo",
            '(($# > 0)) || { echo "usage: sudo command" >&2; exit 1; }\nexec "$@"\n',
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def toolset(self, name, *files):
        directory = self.toolsets / name
        directory.mkdir()
        for file in files:
            (directory / file).write_text(f"{name} {file}\n")

    def install(self, *names):
        return subprocess.run(
            ["bash", str(self.script), *names],
            env=base_env(self.tmp, self.bin, AGENT_TOOLSETS_DIR=str(self.record_dir)),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_record_installs_each_file_under_the_toolsets_name(self):
        self.toolset("go", *FILES)
        result = self.install("go")
        self.assertEqual(result.returncode, 0, result.stderr)
        for file, suffix in FILES.items():
            self.assertEqual((self.record_dir / f"go.{suffix}").read_text(), f"go {file}\n", file)

    def test_record_lists_a_toolset_once_in_install_order(self):
        self.toolset("go", "check.txt")
        self.toolset("node", "smoke.sh")
        self.assertEqual(self.install("go", "node").returncode, 0)
        self.assertEqual(self.install("go").returncode, 0)
        self.assertEqual((self.record_dir / "toolsets.txt").read_text(), "go\nnode\n")

    def test_record_skips_the_files_a_toolset_lacks(self):
        self.toolset("go", "check.txt")
        self.assertEqual(self.install("go").returncode, 0)
        self.assertEqual(sorted(p.name for p in self.record_dir.iterdir()), ["go.check", "toolsets.txt"])

    def test_an_unknown_toolset_is_refused(self):
        result = self.install("nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no toolset nope", result.stderr)


class SharedHelpersTests(unittest.TestCase):
    """A toolset's install.sh, and a script that sources install-toolset.sh, use its
    log, die and apt helpers rather than their own copies (r4.f13)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.toolsets = self.tmp / "toolsets"
        self.toolsets.mkdir()
        self.script = self.toolsets / "install-toolset.sh"
        shutil.copy(SCRIPT, self.script)
        self.bin = self.tmp / "bin"
        self.calls = self.tmp / "apt-calls"
        write_executable(
            self.bin / "sudo",
            '(($# > 0)) || { echo "usage: sudo command" >&2; exit 1; }\nexec "$@"\n',
        )
        # apt-get as the helpers call it: -q, then a known operation; install
        # wants -y and at least one package, as the real one does.
        write_executable(
            self.bin / "apt-get",
            '[[ "$1" == -q ]] || { echo "E: expected -q first" >&2; exit 100; }\n'
            "shift\n"
            'case "$1" in\n'
            "update | clean) ;;\n"
            "install)\n"
            '\t[[ " $* " == *" -y "* ]] || { echo "E: would prompt" >&2; exit 1; }\n'
            '\t[[ -n "$(printf \'%s\\n\' "${@:2}" | grep -v \'^-\')" ]] || { echo "E: nothing to install" >&2; exit 100; } ;;\n'
            '*) echo "E: Invalid operation $1" >&2; exit 100 ;;\n'
            "esac\n"
            'printf \'%s\\n\' "$*" >>"$APT_CALLS"\n',
        )

    def run_bash(self, *args):
        return subprocess.run(
            ["bash", *args],
            env=base_env(self.tmp, self.bin, AGENT_TOOLSETS_DIR=str(self.tmp / "record"), APT_CALLS=str(self.calls)),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )

    def apt_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_a_toolsets_install_sh_gets_the_helpers_under_its_name(self):
        directory = self.toolsets / "demo"
        directory.mkdir()
        (directory / "list.txt").write_text("# a comment\nalpha\nbeta=1.0  # pinned\n\n")
        (directory / "install.sh").write_text(
            'set -euo pipefail\nlog "hello"\ninstall_apt "$(dirname "$0")/list.txt"\n'
            'install_apt_packages /tmp/x.deb\napt clean\n'
        )
        result = self.run_bash(str(self.script), "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("==> demo: hello\n", result.stdout)
        self.assertEqual(self.apt_calls(), [
            "update", "install -y --no-install-recommends --allow-downgrades alpha beta=1.0", "clean",
            "update", "install -y --no-install-recommends --allow-downgrades /tmp/x.deb", "clean",
            "clean",
        ])

    def test_die_stops_a_toolsets_install_sh_under_its_name(self):
        directory = self.toolsets / "demo"
        directory.mkdir()
        (directory / "install.sh").write_text('die "broken"\necho "after die"\n')
        result = self.run_bash(str(self.script), "demo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("==> demo: broken\n", result.stderr)
        self.assertNotIn("after die", result.stdout)

    def test_the_helpers_reach_only_the_toolsets_install_sh(self):
        # A later toolset's apt install (and Homebrew, and any installer) runs
        # without them: an exported apt would shadow the real command there.
        first = self.toolsets / "first"
        first.mkdir()
        (first / "install.sh").write_text('log "ran"\n')
        second = self.toolsets / "second"
        second.mkdir()
        (second / "packages.txt").write_text("delta\n")
        probe = self.tmp / "probe"
        write_executable(self.bin / "apt-get",
                         f'declare -F log apt die >>"{probe}" || true\nexit 0\n')
        result = self.run_bash(str(self.script), "first", "second")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("==> first: ran\n", result.stdout)
        self.assertEqual(probe.read_text() if probe.exists() else "", "")

    def test_build_colmap_refuses_an_empty_build_list(self):
        # build-colmap.sh sources the helpers; an empty list stops it before apt.
        three_d = self.toolsets / "3d"
        three_d.mkdir()
        shutil.copy(REPO / "images/toolsets/3d/build-colmap.sh", three_d / "build-colmap.sh")
        (three_d / "build-packages.txt").write_text("# nothing yet\n\n")
        write_executable(self.bin / "dpkg-query", "exit 0\n")
        result = subprocess.run(
            ["bash", str(three_d / "build-colmap.sh")],
            env=base_env(self.tmp, self.bin, APT_CALLS=str(self.calls), CUDAARCHS="89-real;120",
                         COLMAP_VERSION="4.2.1"),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"==> build-colmap: no packages in {three_d}/build-packages.txt", result.stderr)
        self.assertEqual(self.apt_calls(), [])

    def test_no_toolset_script_restates_them(self):
        """Each install.sh, and each script that sources install-toolset.sh, uses
        its helpers rather than defining its own."""
        import re
        users = [path for path in SCRIPT.parent.rglob("*.sh")
                 if path.name == "install.sh" or "install-toolset.sh\"" in path.read_text()]
        self.assertGreaterEqual(len(users), 5, users)  # 3d, gpu, gis, android, build-colmap.sh at least
        users.append(REPO / "scripts/cloud/lock.sh")  # sources it for github_latest
        restated = [
            f"{path.relative_to(REPO)}:{number}"
            for path in users
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if re.match(r"\s*(log|apt|install_apt|install_apt_packages|github_latest)\s*\(\)", line)
            or (path.name != "lock.sh" and re.match(r"\s*die\s*\(\)", line))
        ]
        self.assertEqual(restated, [])

    def test_only_github_latest_reads_a_release_redirect(self):
        """The /releases/latest lookup is github_latest's alone: no toolset script
        or cloud script does its own (#125's review)."""
        scripts = [*SCRIPT.parent.rglob("*.sh"), *(REPO / "scripts").rglob("*.sh")]
        lookups = [
            f"{path.relative_to(REPO)}:{number}"
            for path in scripts
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if "url_effective" in line and not line.lstrip().startswith("#")
        ]
        self.assertEqual(lookups, ["images/toolsets/install-toolset.sh:" + str(next(
            n for n, line in enumerate(SCRIPT.read_text().splitlines(), 1) if "url_effective" in line))])

    def test_sourcing_defines_the_helpers_and_runs_nothing(self):
        caller = self.tmp / "caller.sh"
        (self.tmp / "list.txt").write_text("gamma\n")
        caller.write_text(
            f'TOOLSET_LOG_NAME=caller\nsource "{self.script}"\nlog "ready"\n'
            f'install_apt "{self.tmp / "list.txt"}"\ndie "done"\n'
        )
        result = self.run_bash(str(caller))
        # No usage error from install-toolset.sh's own main, and the caller's die.
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("usage", result.stderr)
        self.assertEqual((result.stdout.splitlines()[0], result.stderr), ("==> caller: ready", "==> caller: done\n"))
        self.assertIn("install -y --no-install-recommends --allow-downgrades gamma", self.apt_calls())


class GithubLatestTests(unittest.TestCase):
    """install-toolset.sh's github_latest, which a toolset's install.sh gets
    exported, a builder script gets by sourcing, and lock.sh shares, against the
    fake curl test_lock uses (it refuses flags curl wouldn't take)."""

    VGL = "https://github.com/VirtualGL/virtualgl/releases"
    COLMAP = "https://github.com/colmap/colmap/releases"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.toolsets = self.tmp / "toolsets"
        self.toolsets.mkdir()
        self.script = self.toolsets / "install-toolset.sh"
        shutil.copy(SCRIPT, self.script)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "curl", FAKE_CURL)  # Python: not write_executable's bash
        write_executable(self.bin / "sudo", 'exec "$@"\n')
        write_executable(self.bin / "apt-get", 'exit 0\n')  # the gpu toolset's packages.txt
        self.urls = {f"redirect {self.VGL}/latest": f"{self.VGL}/tag/3.1.5"}
        self.log = self.tmp / "curl-log"

    def run_bash(self, *args, **env):
        (self.tmp / "urls.json").write_text(__import__("json").dumps(self.urls))
        return subprocess.run(
            ["bash", *args],
            env=base_env(self.tmp, self.bin, AGENT_TOOLSETS_DIR=str(self.tmp / "record"), FAKE_BIN=str(self.bin),
                         FAKE_URLS=str(self.tmp / "urls.json"), FAKE_LOG=str(self.log), **env),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )

    def demo(self, body):
        # install-toolset.sh puts Homebrew first on PATH where it's installed (a
        # runner, a session), whose curl would reach GitHub: the fakes go back in front.
        directory = self.toolsets / "demo"
        directory.mkdir(exist_ok=True)
        (directory / "install.sh").write_text('set -euo pipefail\nPATH="$FAKE_BIN:$PATH"\n' + body)
        return self.run_bash(str(self.script), "demo")

    def test_a_toolsets_install_sh_reads_the_newest_tag(self):
        result = self.demo(f'version="$(github_latest {self.VGL})"\nlog "VirtualGL $version"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("==> demo: VirtualGL 3.1.5\n", result.stdout)

    def test_a_repository_with_no_release_fails(self):
        # GitHub redirects /releases/latest to /releases when there's none.
        self.urls[f"redirect {self.VGL}/latest"] = self.VGL
        result = self.demo(f'version="$(github_latest {self.VGL})"\nlog "after: $version"\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"==> demo: no latest release in {self.VGL} ({self.VGL})\n", result.stderr)
        self.assertNotIn("after", result.stdout)

    def test_a_failed_lookup_names_its_url_after_retrying(self):
        self.urls[f"status {self.VGL}/latest"] = [503] * 9
        result = self.demo(f'version="$(github_latest {self.VGL})"\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"==> demo: couldn't fetch {self.VGL}/latest\n", result.stderr)
        self.assertEqual(self.log.read_text().splitlines().count(f"attempt {self.VGL}/latest"), 4)

    def test_the_gpu_toolset_reads_virtualgl_through_it(self):
        # Its install.sh with the helpers exported as install-toolset.sh exports
        # them, without the Homebrew PATH (see demo).
        self.urls.pop(f"redirect {self.VGL}/latest")
        runner = self.tmp / "run-gpu.sh"
        runner.write_text(
            f'TOOLSET_LOG_NAME=gpu\nsource "{self.script}"\n'
            "export -f log die apt install_apt install_apt_packages github_latest\n"
            f'TOOLSET_LOG_NAME=gpu bash "{REPO / "images/toolsets/gpu/install.sh"}"\n'
        )
        result = self.run_bash(str(runner), CUDAARCHS="89-real;120")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"==> gpu: couldn't fetch {self.VGL}/latest\n", result.stderr)
        self.assertIn(f"attempt {self.VGL}/latest", self.log.read_text())

    def test_build_colmap_reads_colmap_through_it(self):
        three_d = self.toolsets / "3d"
        three_d.mkdir()
        shutil.copy(REPO / "images/toolsets/3d/build-colmap.sh", three_d / "build-colmap.sh")
        (three_d / "build-packages.txt").write_text("# nothing yet\n")
        self.urls[f"redirect {self.COLMAP}/latest"] = f"{self.COLMAP}/tag/4.2.1"
        result = self.run_bash(str(three_d / "build-colmap.sh"), CUDAARCHS="89-real;120")
        # The newest tag read, it goes on to the (empty) build list and stops.
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"head {self.COLMAP}/latest", self.log.read_text())
        self.assertIn(f"==> build-colmap: no packages in {three_d}/build-packages.txt", result.stderr)


if __name__ == "__main__":
    unittest.main()
