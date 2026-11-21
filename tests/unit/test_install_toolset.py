"""images/toolsets/install-toolset.sh: what `record` leaves in the record directory."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable
from test_lock import FAKE_CURL, executable

SCRIPT = REPO / "images/toolsets/install-toolset.sh"

# A releases base followed by /latest, in code that reads one: a URL
# (github.com/o/r/releases/latest) or a variable holding the base
# ("$TFLINT_RELEASES/latest/download"). claude-code-releases/latest, a download
# channel, is neither.
LATEST_URL = re.compile(r"(?:/releases|_RELEASES\}?\"?)/latest\b")

# apt-get as the helpers call it: -q, then a known operation; install wants -y
# and at least one package, as the real one does. Each call goes to $APT_CALLS.
FAKE_APT_GET = (
    '[[ "$1" == -q ]] || { echo "E: expected -q first" >&2; exit 100; }\n'
    "shift\n"
    'case "$1" in\n'
    "update | clean) ;;\n"
    "install)\n"
    '\t[[ " $* " == *" -y "* ]] || { echo "E: would prompt" >&2; exit 1; }\n'
    '\t[[ -n "$(printf \'%s\\n\' "${@:2}" | grep -v \'^-\')" ]] || { echo "E: nothing to install" >&2; exit 100; } ;;\n'
    '*) echo "E: Invalid operation $1" >&2; exit 100 ;;\n'
    "esac\n"
    'printf \'%s\\n\' "$*" >>"$APT_CALLS"\n'
)

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
        write_executable(self.bin / "apt-get", FAKE_APT_GET)

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

    def test_a_missing_list_fails_install_apt(self):
        # install_apt reads the list in $(apt_list ...), where errexit is off:
        # apt_list has to return the failure, or the list reads as empty and
        # nothing is installed, green.
        directory = self.toolsets / "demo"
        directory.mkdir()
        (directory / "install.sh").write_text(
            f'set -euo pipefail\ninstall_apt "{self.tmp}/nope.txt"\necho "after install_apt"\n')
        result = self.run_bash(str(self.script), "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("after install_apt", result.stdout)
        self.assertIn("nope.txt", result.stderr)
        self.assertEqual(self.apt_calls(), [])

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
        for script in ("build-colmap.sh", "runtime-packages.sh"):
            shutil.copy(REPO / "images/toolsets/3d" / script, three_d / script)
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
        users = [path for path in SCRIPT.parent.rglob("*.sh")
                 if path.name == "install.sh" or "install-toolset.sh\"" in path.read_text()]
        self.assertGreaterEqual(len(users), 5, users)  # 3d, gpu, gis, android, build-colmap.sh at least
        users.append(REPO / "scripts/cloud/lock.sh")  # sources it for github_latest
        restated = [
            f"{path.relative_to(REPO)}:{number}"
            for path in users
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if re.match(r"\s*(log|apt|apt_update|install_apt|install_apt_packages|github_latest)\s*\(\)", line)
            or (path.name != "lock.sh" and re.match(r"\s*die\s*\(\)", line))
        ]
        self.assertEqual(restated, [])
        # Nor its output format, whatever name it prints: log's prefix, by hand
        # (r5.f15). Every toolset script, not only the ones that use the helpers.
        prefixed = [
            f"{path.relative_to(REPO)}:{number}"
            for path in sorted(SCRIPT.parent.rglob("*.sh"))
            if path != SCRIPT
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if re.search(r"""['"]==> """, line) and not line.lstrip().startswith("#")
        ]
        self.assertEqual(prefixed, [])

    def test_only_github_latest_reads_a_release_redirect(self):
        """The /releases/latest lookup is github_latest's alone: no other script in
        the repo, and no workflow, does its own (#125's review), and none builds a
        URL under a releases base's /latest, whatever the base is called (r5.f14:
        "$TFLINT_RELEASES/latest/download" has no "/releases/" in it)."""
        tracked = subprocess.run(["git", "ls-files", "*.sh", ".github/workflows/*.yml"], cwd=REPO,
                                 check=True, capture_output=True, text=True).stdout.split()
        self.assertGreater(len(tracked), 20, tracked)
        own = SCRIPT.relative_to(REPO).as_posix()
        def code_lines(name):
            for number, line in enumerate((REPO / name).read_text().splitlines(), 1):
                if not line.lstrip().startswith("#"):
                    yield number, line

        lookups = [f"{name}:{number}" for name in tracked if name != own
                   for number, line in code_lines(name) if "url_effective" in line or LATEST_URL.search(line)]
        self.assertEqual(lookups, [])
        # And it is still there, so the check above isn't looking at nothing.
        self.assertEqual(len([line for _, line in code_lines(own) if "url_effective" in line]), 1)

    def test_the_latest_url_check_sees_each_spelling(self):
        # The pattern itself, on the spellings found so far.
        for line in ('release="$TFLINT_RELEASES/latest/download"',
                     'curl -fsSL "${TFLINT_RELEASES}/latest/download/x"',
                     "curl https://github.com/o/r/releases/latest/download/x"):
            with self.subTest(line=line):
                self.assertTrue(LATEST_URL.search(line))
        self.assertFalse(LATEST_URL.search("curl https://downloads.claude.ai/claude-code-releases/latest"))

    def test_sourcing_it_only_defines_functions(self):
        """lock.sh sources it in the lock watch and the deploy, so above its return
        there is nothing but function definitions: no variable, and no cd."""
        probe = self.tmp / "probe.sh"
        probe.write_text(
            'before="$(compgen -v | LC_ALL=C sort)"\nstart="$PWD"\n'
            f'source "{self.script}"\n'
            'after="$(compgen -v | LC_ALL=C sort)"\n'
            'LC_ALL=C comm -13 <(printf "%s\\n" "$before") <(printf "%s\\n" "$after") | grep -vxE "before|start|BASH_.*|PIPESTATUS|_" || true\n'
            '[[ "$PWD" == "$start" ]] || echo "cd to $PWD"\n'
            'declare -F | sed "s/^declare -f //" | LC_ALL=C sort | tr "\\n" " "\n'
        )
        result = self.run_bash(str(probe))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "apt apt_entries apt_install_options apt_list apt_update brew_env brew_path "
                         "brewfile_formulae die github_latest install_apt install_apt_packages log trust_taps ")

    def helper(self, script, stdin=""):
        return subprocess.run(["bash", "-c", f'set -euo pipefail; source "{self.script}"; {script}'],
                              env=base_env(self.tmp, self.bin), input=stdin, capture_output=True, text=True,
                              timeout=30)

    def test_apt_entries_reads_each_line_once_for_every_reader(self):
        # r3.f20: the build (install_apt, apt-prepare.sh --list) and the lock
        # (packages_of, the pin readers) read a list through these alone.
        listing = ("# a comment\n\n   \ncurl\n  sudo  \t\nopenssl=3.0.13-0ubuntu3.6  # pinned: - snapshot=20260101T000000Z\n"
                   "  # indented comment\nzlib1g#pinned:x\n")
        entries = self.helper("apt_entries", listing)
        self.assertEqual(entries.returncode, 0, entries.stderr)
        self.assertEqual(entries.stdout, "curl\t\nsudo\t\nopenssl=3.0.13-0ubuntu3.6\t pinned: - snapshot=20260101T000000Z\n"
                                         "zlib1g\tpinned:x\n")
        names = self.helper("apt_list", listing)
        self.assertEqual(names.stdout, "curl\nsudo\nopenssl=3.0.13-0ubuntu3.6\nzlib1g\n")
        self.assertEqual(self.helper("apt_list", "# none\n\n").stdout, "")
        missing = self.helper(f'apt_list "{self.tmp}/nope"')
        self.assertNotEqual(missing.returncode, 0, "a missing list isn't an empty one")

    def test_trust_taps_taps_and_trusts_each_named_tap_once(self):
        calls = self.tmp / "brew-calls"
        write_executable(self.bin / "brew", f"""case "$1" in
tap) (($# == 2)) || exit 1 ;;
trust) [[ $# == 3 && $2 == --tap ]] || {{ echo "brew: unknown trust usage" >&2; exit 1; }} ;;
*) echo "brew: unexpected $*" >&2; exit 1 ;;
esac
echo "$*" >>"{calls}"
""")
        one, two = self.tmp / "a.Brewfile", self.tmp / "b.Brewfile"
        one.write_text('tap "lolay/tap"\nbrew "go"\n')
        two.write_text('tap "lolay/tap"\ntap "osgeo/osgeo4mac", trusted: true\nbrew "lolay/tap/nowline"\n')
        result = self.helper(f'trust_taps "{one}" "{two}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.read_text().splitlines(), [
            "tap lolay/tap", "trust --tap lolay/tap", "tap osgeo/osgeo4mac", "trust --tap osgeo/osgeo4mac"])
        (self.tmp / "none.Brewfile").write_text('brew "go"\n')
        calls.unlink()
        self.assertEqual(self.helper(f'trust_taps "{self.tmp}/none.Brewfile"').returncode, 0)
        self.assertFalse(calls.exists())

    def test_no_script_reads_a_list_or_a_brewfile_its_own_way(self):
        # r3.f20: the lock is right only while it reads what the build installs,
        # so install-toolset.sh's readers are the only ones: no sed or grep over a
        # package list's lines, and no tap or formula list of a Brewfile.
        readers = []
        # The Cloud Run images and their lock, and the macOS image, which taps
        # its Brewfiles' taps through trust_taps too.
        paths = [REPO / "scripts/cloud/lock.sh", *sorted((REPO / "images/toolsets").rglob("*.sh")),
                 *sorted((REPO / "images/cloud").rglob("*.sh")), *sorted((REPO / "images/cloud").glob("*/Dockerfile")),
                 *sorted((REPO / "images/macos/scripts").glob("*.sh"))]
        for path in paths:
            if path == SCRIPT:
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                code = line.split(" #", 1)[0]
                if line.lstrip().startswith("#"):
                    continue
                if (re.search(r"\b(sed|grep|awk)\b.*(packages\.txt|package_lines|s/#\.\*//)", code)
                        or re.search(r"#\*\}", code)
                        or re.search(r"\^tap|brew tap |brew bundle list", code)):
                    readers.append(f"{path.relative_to(REPO)}:{number}")
        self.assertEqual(readers, [])

# brew as install_brewfile calls it, through AGENT_BREW. Like the real one,
# `list --versions` refuses a name that isn't an installed formula (the empty
# name included), and anything else unexpected fails. Each call goes to
# $BREW_CALLS.
FAKE_BREW = r'''#!/bin/bash
printf '%s\n' "$*" >>"$BREW_CALLS"
case "$1" in
shellenv) printf 'export PATH="%s:$PATH"\n' "$(dirname "$0")" ;;
update) [[ "$*" == "update --quiet" ]] || exit 1 ;;
bundle)
	if [[ "$2" == list ]]; then
		[[ "$3" == --formula && "$4" == --file=* ]] || exit 1
		sed -n '/OS.mac?/d; s/^brew "\([^"]*\)".*/\1/p' "${4#--file=}"
	else
		[[ "$2" == --file=* ]] || exit 1
	fi
	;;
list)
	[[ "$2" == --versions && $# -gt 2 ]] || { echo "brew: unexpected $*" >&2; exit 1; }
	for name in "${@:3}"; do
		[[ -n "$name" ]] || { echo "Error: No available formula with the name \"\"." >&2; exit 1; }
		echo "$name 1.0"
	done
	;;
cleanup) ;;
--cache) printf '%s\n' "$BREW_CACHE" ;;
*) echo "brew: unexpected $*" >&2; exit 1 ;;
esac
'''


class InstallBrewfileTests(unittest.TestCase):
    """install_brewfile, against a fake brew."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.toolsets = self.tmp / "toolsets"
        self.toolsets.mkdir()
        self.script = self.toolsets / "install-toolset.sh"
        shutil.copy(SCRIPT, self.script)
        self.bin = self.tmp / "bin"
        write_executable(self.bin / "sudo", '(($# > 0)) || exit 1\nexec "$@"\n')
        self.brew = self.tmp / "brew/bin/brew"
        write_executable(self.brew, FAKE_BREW.split("\n", 1)[1])
        self.calls = self.tmp / "brew-calls"

    def install(self, brewfile):
        (self.toolsets / "demo").mkdir()
        (self.toolsets / "demo/Brewfile").write_text(brewfile)
        return subprocess.run(
            ["bash", str(self.script), "demo"],
            env=base_env(self.tmp, self.bin, AGENT_TOOLSETS_DIR=str(self.tmp / "record"), AGENT_BREW=str(self.brew),
                         BREW_CALLS=str(self.calls), BREW_CACHE=str(self.tmp / "cache")),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)

    def test_the_installed_formulae_are_logged(self):
        result = self.install('brew "go"\nbrew "git" if OS.mac?\nbrew "node"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("==> install-toolset: go 1.0;node 1.0;\n", result.stdout)

    def test_a_brewfile_with_no_formula_here_installs(self):
        for brewfile in ('brew "git" if OS.mac?\n', 'cask "firefox"\n', "# nothing\n"):
            with self.subTest(brewfile):
                shutil.rmtree(self.toolsets / "demo", ignore_errors=True)
                self.calls.unlink(missing_ok=True)
                result = self.install(brewfile)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls.read_text().splitlines()
                self.assertFalse([call for call in calls if call.startswith("list")])
                self.assertIn("cleanup --prune=all -s", calls)


class MacosTapsTests(unittest.TestCase):
    """The macOS image taps and trusts its Brewfiles' taps with trust_taps, from
    install-toolset.sh staged beside them. Its scripts run under macOS's
    /bin/bash, 3.2, which install-toolset.sh is read with too."""

    PACKER = REPO / "images/macos/build.pkr.hcl"
    INSTALL = REPO / "images/macos/scripts/install-packages.sh"
    # Syntax newer than bash 3.2: a sourced file using any of it fails to load
    # there (|&, ;& and ;;&, &>>, coproc, case-changing expansions), and a
    # function using these builtins or declarations fails when it runs.
    NEWER_SYNTAX = re.compile(r"\|&|;;?&|&>>|\bcoproc\b|\$\{[#!]?[A-Za-z_][A-Za-z_0-9]*(\[[^]]*\])?(\^|,)")
    NEWER_BUILTINS = re.compile(r"\b(mapfile|readarray|declare -[A-Za-z]*[An]|local -[A-Za-z]*[An]|wait -n)\b")

    def test_the_image_stages_install_toolset_and_install_packages_sources_it(self):
        packer = self.PACKER.read_text()
        self.assertRegex(packer, r'source\s*=\s*"\$\{path\.root\}/\.\./toolsets/install-toolset\.sh"\n'
                                 r'\s*destination\s*=\s*"\$\{local\.guest_staging\}/install-toolset\.sh"')
        script = self.INSTALL.read_text()
        self.assertIn('\nsource "$STAGING_DIR/install-toolset.sh"\ntrust_taps "${brewfiles[@]}"\n', script)

    def sourced_part(self):
        text = SCRIPT.read_text()
        guard = '[[ "${BASH_SOURCE[0]}" == "$0" ]] || return 0\n'
        self.assertIn(guard, text)
        return text.split(guard, 1)[0]

    def test_what_sourcing_reads_is_bash_3_2(self):
        code = "\n".join(line.split(" #", 1)[0] for line in self.sourced_part().splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertIsNone(self.NEWER_SYNTAX.search(code))

    def test_trust_taps_runs_on_bash_3_2(self):
        body = re.search(r"^trust_taps\(\) \{\n(.*?)^\}\n", self.sourced_part(), re.S | re.M)
        self.assertIsNotNone(body)
        self.assertIsNone(self.NEWER_BUILTINS.search(body.group(1)))
        self.assertIsNone(self.NEWER_SYNTAX.search(body.group(1)))

    def test_the_checks_see_what_they_must(self):
        for code in ("a |& b", "case x in a) b ;& esac", "x;;&", "a &>> f", "coproc a", "${x^^}", "${x,}",
                     "${a[0]^}"):
            with self.subTest(code):
                self.assertIsNotNone(self.NEWER_SYNTAX.search(code))
        for code in ("a || b", "a && b", "${x:-y}", "${#x}", "a >&2", "a 2>&1 | b"):
            with self.subTest(code):
                self.assertIsNone(self.NEWER_SYNTAX.search(code))
        for code in ("mapfile -t x", "readarray x", "declare -A m", "local -n r=x"):
            with self.subTest(code):
                self.assertIsNotNone(self.NEWER_BUILTINS.search(code))


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
            "export -f log die apt apt_install_options install_apt install_apt_packages github_latest\n"
            f'TOOLSET_LOG_NAME=gpu bash "{REPO / "images/toolsets/gpu/install.sh"}"\n'
        )
        result = self.run_bash(str(runner), CUDAARCHS="89-real;120")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"==> gpu: couldn't fetch {self.VGL}/latest\n", result.stderr)
        self.assertIn(f"attempt {self.VGL}/latest", self.log.read_text())

    def test_the_terraform_toolset_downloads_the_one_release_latest_resolved_to(self):
        # tflint's latest is read once, through github_latest, and both downloads
        # (the zip and its checksums) are of that tag: a release landing between
        # two /latest/download fetches would pair a zip with the wrong checksums
        # (r5.f14). The fakes take only the calls install.sh makes.
        tflint = "https://github.com/terraform-linters/tflint/releases"
        self.urls = {
            f"redirect {tflint}/latest": f"{tflint}/tag/v0.65.0",
            f"{tflint}/download/v0.65.0/tflint_linux_amd64.zip": "zip",
            f"{tflint}/download/v0.65.0/checksums.txt": "sums",
        }
        write_executable(self.bin / "tenv", '[[ "$1 $2" == "tf install" || "$1 $2" == "tf use" ]] && (($# == 3)) || exit 2\n')
        write_executable(self.bin / "sha256sum", '[[ "$*" == "--check --ignore-missing --quiet checksums.txt" && -f checksums.txt ]] || exit 2\n')
        write_executable(self.bin / "unzip", '[[ "$1 $2 $4 $5" == "-q -o tflint -d" && -f "$3" ]] || exit 2\ntouch "$6/tflint"\n')
        write_executable(self.bin / "sudo", '[[ "$1 $2 $3 $5" == "install -m 755 /usr/local/bin/tflint" && -f "$4" ]] || exit 2\necho "$*" >>"$FAKE_LOG.sudo"\n')
        write_executable(self.bin / "terraform", 'echo "Terraform v1.17.1"\n')
        write_executable(self.bin / "tflint", 'echo "TFLint version 0.65.0"\n')
        runner = self.tmp / "run-terraform.sh"
        runner.write_text(
            f'source "{self.script}"\n'
            "export -f log die apt apt_install_options install_apt install_apt_packages github_latest\n"
            f'TOOLSET_LOG_NAME=terraform bash "{REPO / "images/toolsets/terraform/install.sh"}"\n'
        )
        result = self.run_bash(str(runner))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("==> terraform: Terraform v1.17.1, TFLint version 0.65.0\n", result.stdout)
        self.assertEqual(
            [line for line in self.log.read_text().splitlines() if line.startswith(("head ", "get "))],
            [f"head {tflint}/latest", f"get {tflint}/download/v0.65.0/tflint_linux_amd64.zip",
             f"get {tflint}/download/v0.65.0/checksums.txt"])

    def test_build_colmap_reads_colmap_through_it(self):
        three_d = self.toolsets / "3d"
        three_d.mkdir()
        for script in ("build-colmap.sh", "runtime-packages.sh"):
            shutil.copy(REPO / "images/toolsets/3d" / script, three_d / script)
        (three_d / "build-packages.txt").write_text("# nothing yet\n")
        self.urls[f"redirect {self.COLMAP}/latest"] = f"{self.COLMAP}/tag/4.2.1"
        result = self.run_bash(str(three_d / "build-colmap.sh"), CUDAARCHS="89-real;120")
        # The newest tag read, it goes on to the (empty) build list and stops.
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"head {self.COLMAP}/latest", self.log.read_text())
        self.assertIn(f"==> build-colmap: no packages in {three_d}/build-packages.txt", result.stderr)


class GpuGencodeTests(unittest.TestCase):
    """gpu/install.sh's CUDAARCHS to nvcc -gencode translation, through its
    --print-gencode mode, which installs nothing (r5.f6). The helpers come
    exported, as install-toolset.sh exports them."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.runner = self.tmp / "run.sh"
        self.runner.write_text(
            f'source "{SCRIPT}"\nexport -f log die\n'
            f'TOOLSET_LOG_NAME=gpu bash "{REPO / "images/toolsets/gpu/install.sh"}" "$@"\n')

    def gencode(self, archs):
        return subprocess.run(
            ["bash", str(self.runner), "--print-gencode"],
            env=base_env(self.tmp, self.tmp / "bin", CUDAARCHS=archs, AGENT_TOOLSETS_DIR=str(self.tmp / "record")),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )

    @staticmethod
    def flags(*pairs):
        return " ".join(f"-gencode arch=compute_{cc},code={code}" for cc, code in pairs) + "\n"

    def test_real_is_sass_and_the_plain_entry_is_sass_and_ptx(self):
        result = self.gencode("89-real;120")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.flags(("89", "sm_89"), ("120", "sm_120"), ("120", "compute_120")))

    def test_virtual_is_ptx_alone(self):
        result = self.gencode("89-real;120-virtual")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.flags(("89", "sm_89"), ("120", "compute_120")))

    def test_a_plain_entry_is_both(self):
        result = self.gencode("89")
        self.assertEqual(result.stdout, self.flags(("89", "sm_89"), ("89", "compute_89")))

    def test_a_bad_entry_dies_before_anything_is_installed(self):
        for archs in ("abc", "89-bogus", "8.9", ";89", "-real", "89-real;12x"):
            with self.subTest(archs):
                result = self.gencode(archs)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(result.stdout, "")
                self.assertIn("==> gpu: unexpected CUDAARCHS entry '", result.stderr)
                self.assertIn(f"in '{archs}'", result.stderr)


class ColmapRuntimePackagesTests(unittest.TestCase):
    """3d/runtime-packages.sh, the end of build-colmap.sh: the packages the
    built binary links, as the image's install will install them (r5.f6), with
    fakes for ldd and dpkg that refuse what the real ones would."""

    FAKE_LDD = r'''[[ $# -eq 1 && "$1" == "$LDD_BIN" ]] || { echo "ldd: $*: No such file or directory" >&2; exit 1; }
cat "$LDD_OUT"
'''
    FAKE_DPKG = r'''[[ $# -eq 2 && "$1" == -S ]] || { echo "dpkg: error: unexpected arguments: $*" >&2; exit 2; }
path="$2"
while IFS=' ' read -r known package; do
	[[ "$known" == "$path" ]] && { printf '%s: %s\n' "$package" "$path"; exit 0; }
done <"$DPKG_OWNERS"
echo "dpkg-query: no path found matching pattern $path" >&2
exit 1
'''
    FAKE_DPKG_QUERY = r'''if [[ $# -ne 2 || "$1" != -W || "$2" != '-f=${Package}=${Version}\n' ]]; then
    echo "dpkg-query: error: unexpected arguments ($# given): $*" >&2
    exit 2
fi
cat "$DPKG_STATE"
'''

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        write_executable(self.bin / "sudo", 'exec "$@"\n')
        write_executable(self.bin / "ldd", self.FAKE_LDD)
        write_executable(self.bin / "dpkg", self.FAKE_DPKG)
        write_executable(self.bin / "dpkg-query", self.FAKE_DPKG_QUERY)
        self.colmap = self.tmp / "colmap"
        self.colmap.write_text("")
        self.libs = {}
        for name in ("libfoo.so.1", "libbar.so.2", "libfoo-extra.so.1"):
            (self.tmp / name).write_text("")
            self.libs[name] = os.path.realpath(self.tmp / name)
        self.ldd = ""
        self.owners = {}
        self.list = self.tmp / "share/colmap/runtime-packages.txt"

    def run_list(self, from_build_list, body=None):
        (self.tmp / "ldd-out").write_text(self.ldd)
        (self.tmp / "owners").write_text("".join(f"{path} {package}:amd64\n" for path, package in self.owners.items()))
        runner = self.tmp / "run.sh"
        runner.write_text(
            "set -euo pipefail\n"
            f'source "{SCRIPT}"\nsource "{REPO / "images/toolsets/3d/runtime-packages.sh"}"\n'
            + (body or f'write_runtime_packages "{self.colmap}" "{self.list}" "$FROM_BUILD_LIST"\n'))
        return subprocess.run(
            ["bash", str(runner)],
            env=base_env(self.tmp, self.bin, TOOLSET_LOG_NAME="build-colmap", FROM_BUILD_LIST=from_build_list,
                         LDD_BIN=str(self.colmap), LDD_OUT=str(self.tmp / "ldd-out"),
                         DPKG_OWNERS=str(self.tmp / "owners"), DPKG_STATE=str(self.tmp / "dpkg-state")),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )

    def link(self, *names):
        self.ldd = "\tlinux-vdso.so.1 (0x00007ffc)\n" + "".join(
            f"\t{name} => {self.libs[name]} (0x00007f00)\n" for name in names
        ) + "\t/lib64/ld-linux-x86-64.so.2 (0x00007f01)\n"

    def test_built_packages_are_pinned_and_the_bases_are_by_name(self):
        self.link("libfoo.so.1", "libbar.so.2", "libfoo-extra.so.1")
        self.owners = {self.libs["libfoo.so.1"]: "libfoo1", self.libs["libbar.so.2"]: "libbar2",
                       self.libs["libfoo-extra.so.1"]: "libfoo1"}  # two libraries, one package
        # libfoo1 was installed by the build list; a package that merely starts
        # with the same name was too, and is no match.
        result = self.run_list("libfoo1=1.2-3\nlibfoo10=9\nunrelated=1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.list.read_text(), "libbar2\nlibfoo1=1.2-3\n")
        self.assertIn("==> build-colmap: runtime packages: libbar2 libfoo1=1.2-3\n", result.stdout)

    def test_a_package_the_build_list_didnt_install_stays_a_bare_name(self):
        self.link("libbar.so.2")
        self.owners = {self.libs["libbar.so.2"]: "libbar2"}
        result = self.run_list("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.list.read_text(), "libbar2\n")

    def test_a_library_linked_under_usr_is_found_under_lib(self):
        # dpkg records some libraries under /lib while ldd names /usr/lib.
        real = os.path.realpath("/usr/bin/env")
        self.assertTrue(real.startswith("/usr/"), real)
        self.libs["libusr.so"] = real
        self.link("libusr.so")
        self.owners = {real[len("/usr"):]: "coreutils"}
        result = self.run_list("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.list.read_text(), "coreutils\n")

    def test_a_library_no_package_owns_stops_the_build(self):
        self.link("libfoo.so.1", "libbar.so.2")
        self.owners = {self.libs["libbar.so.2"]: "libbar2"}
        result = self.run_list("")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"==> build-colmap: no package owns {self.libs['libfoo.so.1']}", result.stderr)
        self.assertFalse(self.list.exists())

    def test_a_binary_with_no_linked_libraries_stops_the_build(self):
        self.link()
        result = self.run_list("")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"==> build-colmap: found no packages for {self.colmap}'s libraries", result.stderr)
        self.assertFalse(self.list.exists())

    def test_a_library_that_isnt_installed_stops_the_build(self):
        self.link("libbar.so.2")
        self.ldd += "\tlibmissing.so.3 => not found\n"
        self.owners = {self.libs["libbar.so.2"]: "libbar2"}
        result = self.run_list("")
        self.assertEqual(result.returncode, 1)
        self.assertIn("==> build-colmap: colmap links a library that isn't installed", result.stderr)
        self.assertIn("libmissing.so.3 => not found", result.stderr)
        self.assertFalse(self.list.exists())

    def test_what_the_build_list_installed_is_what_changed_in_dpkg(self):
        # build-colmap.sh's before and after: a new package and an upgraded one.
        state = self.tmp / "dpkg-state"
        state.write_text("libbar2=2.0\nlibfoo1=1.0\n")
        body = ('before="$(installed_packages)"\n'
                f'printf "libbar2=2.0\\nlibfoo1=1.2-3\\nlibnew=4\\n" >"{state}"\n'
                'packages_changed_since "$before"\n')
        result = self.run_list("", body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "libfoo1=1.2-3\nlibnew=4\n")


if __name__ == "__main__":
    unittest.main()
