"""The toolsets' environment (r1.f27, f28): images/toolsets/toolset-profile.sh, the
one assembly both Linux images' profiles source; the records the jvm, rust, go and
android toolsets leave for it; install-toolset.sh's toolset_env, which a toolset's
install.sh reads another's record with; and that every variable a toolset's
CLAUDE.md promises is one a toolset records."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO, base_env, write_executable

TOOLSETS = REPO / "images/toolsets"
INSTALL_TOOLSET = TOOLSETS / "install-toolset.sh"
TOOLSET_PROFILE = TOOLSETS / "toolset-profile.sh"
PROFILES = {
    "cloud": REPO / "images/cloud/runner/profile.sh",
    "android": REPO / "images/android/files/profile.sh",
}
FAKE_SUDO = '(($# > 0)) || { echo "usage: sudo command" >&2; exit 1; }\nexec "$@"\n'


def toolset_dirs():
    return sorted(path for path in TOOLSETS.iterdir() if path.is_dir() and path.name != "skills")


class ProfileAssemblyTests(unittest.TestCase):
    """Both images' profiles build the session's PATH and environment from the
    toolsets' record alone, the same way."""

    HOME = "/home/agent"
    BREW = "/opt/brew"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.record = self.tmp / "toolsets"
        self.record.mkdir()

    def run_profile(self, image, then, *, times=1):
        env = {"HOME": self.HOME, "PATH": "/usr/bin:/bin", "HOMEBREW_PREFIX": self.BREW,
               "AGENT_SCRATCH": "/scratch", "AGENT_TOOLSETS_DIR": str(self.record),
               "AGENT_TOOLSET_PROFILE": str(TOOLSET_PROFILE),
               "AGENT_GH_SHIM_PROFILE": str(self.tmp / "no-shim"), "PROFILE": str(PROFILES[image])}
        result = subprocess.run(["/bin/sh", "-c", '. "$PROFILE"; ' * times + then], env=env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def path(self, image, **kwargs):
        return self.run_profile(image, 'printf %s "$PATH"', **kwargs).split(":")

    def record_real(self, name, *, path=None):
        """A toolset's record as install-toolset.sh leaves it: its own profile.sh,
        and the PATH entries its install.sh would write."""
        shutil.copy(TOOLSETS / name / "profile.sh", self.record / f"{name}.profile.sh")
        if path:
            (self.record / f"{name}.path").write_text(f"# written by install.sh\n{path}\n")

    def test_both_images_put_the_recorded_paths_between_home_and_homebrew(self):
        (self.record / "jvm.path").write_text("/usr/lib/jvm/java-21/bin\n")
        (self.record / "android.path").write_text("/opt/sdk/platform-tools\n\n/opt/sdk/emulator\n")
        self.record_real("rust", path=f"{self.BREW}/opt/rustup/bin")
        front = [f"{self.HOME}/bin", f"{self.HOME}/.local/bin", "/opt/sdk/platform-tools", "/opt/sdk/emulator",
                 "/usr/lib/jvm/java-21/bin", f"{self.BREW}/opt/rustup/bin", f"{self.BREW}/bin", f"{self.BREW}/sbin",
                 "/usr/bin", "/bin"]
        for image in PROFILES:
            with self.subTest(image=image):
                self.assertEqual(self.path(image)[: len(front)], front)

    def test_both_images_source_the_recorded_profiles(self):
        (self.record / "android.profile.sh").write_text("export ANDROID_HOME=/opt/sdk\n")
        for image in PROFILES:
            with self.subTest(image=image):
                self.assertEqual(self.run_profile(image, 'printf %s "$ANDROID_HOME"'), "/opt/sdk")

    def test_an_image_profile_states_no_toolsets_facts(self):
        # With nothing recorded, nothing of a toolset's is set: the SDK, the JDK
        # and their tools come from the android and jvm toolsets' records.
        for image in PROFILES:
            with self.subTest(image=image):
                out = self.run_profile(image, 'printf "%s|%s|%s|%s" "${ANDROID_HOME-}" "${ANDROID_SDK_ROOT-}" '
                                              '"${JAVA_HOME-}" "$PATH"')
                android_home, sdk_root, java_home, path = out.split("|")
                self.assertEqual((android_home, sdk_root, java_home), ("", "", ""))
                self.assertEqual(path, f"{self.HOME}/bin:{self.HOME}/.local/bin:{self.BREW}/bin:{self.BREW}/sbin"
                                       ":/usr/bin:/bin")

    def test_go_takes_the_first_gopath_entrys_bin(self):
        self.record_real("go")
        for image in PROFILES:
            with self.subTest(image=image):
                out = self.run_profile(image, 'export GOPATH=/work/go:/other; . "$PROFILE"; printf %s "$PATH"',
                                       times=0)
                self.assertEqual(out.split(":")[-1], "/work/go/bin")

    def test_a_path_the_caller_took_over_is_left_as_it_is(self):
        # A test puts a fake first and runs a bash script; on the cloud images that
        # bash sources the profile again (BASH_ENV), with the marker already set.
        self.record_real("rust", path=f"{self.BREW}/opt/rustup/bin")
        self.record_real("go")
        (self.record / "jvm.path").write_text("/usr/lib/jvm/java-21/bin\n")
        for image in PROFILES:
            with self.subTest(image=image):
                out = self.run_profile(image, 'export PATH=/fake:/usr/bin; . "$PROFILE"; printf %s "$PATH"')
                self.assertEqual(out, "/fake:/usr/bin")

    def test_rust_and_go_install_bins_go_at_the_back_once(self):
        self.record_real("rust", path=f"{self.BREW}/opt/rustup/bin")
        self.record_real("go")
        expected = {"cloud": ["/scratch/cache/cargo/bin", f"{self.HOME}/go/bin"],
                    "android": [f"{self.HOME}/.cargo/bin", f"{self.HOME}/go/bin"]}
        for image in PROFILES:
            with self.subTest(image=image):
                for times in (1, 3):
                    path = self.path(image, times=times)
                    self.assertEqual(sorted(path[-2:]), sorted(expected[image]))
                    self.assertEqual(len(path), len(set(path)), f"duplicates in {path}")


class JvmRecordTests(unittest.TestCase):
    """The jvm toolset records the JDK its packages.txt installed as JAVA_HOME and
    its bin, whatever javac is first on PATH."""

    # dpkg -L as the real one answers it: each named package's files, and an
    # error for a package that isn't installed or an option it doesn't know.
    FAKE_DPKG = r"""
[[ "$1" == -L ]] || { echo "dpkg: error: unknown option $1" >&2; exit 2; }
shift
(($# > 0)) || { echo "dpkg-query: --listfiles needs at least one package name" >&2; exit 2; }
for package in "$@"; do
	[[ "$package" == "$FAKE_JDK_PACKAGE" ]] || {
		echo "dpkg-query: package '$package' is not installed" >&2
		exit 1
	}
	printf '%s\n' /. /usr/lib/jvm "$FAKE_JDK" "$FAKE_JDK/bin" "$FAKE_JDK/bin/java"
	[[ -n "${FAKE_JDK_NO_JAVAC:-}" ]] || printf '%s\n' "$FAKE_JDK/bin/javac"
done
"""
    PACKAGES = [entry for entry in (line.split("#")[0].strip()
                                    for line in (TOOLSETS / "jvm/packages.txt").read_text().splitlines()) if entry]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.record = self.tmp / "record"
        self.record.mkdir()
        self.bin = self.tmp / "bin"
        write_executable(self.bin / "sudo", FAKE_SUDO)
        write_executable(self.bin / "dpkg", self.FAKE_DPKG)
        self.jdk = self.tmp / "jvm/java-21-openjdk-test"
        # Another JDK's javac first on PATH: a hosted runner's Temurin, a
        # Homebrew openjdk.
        self.other = self.tmp / "jvm/temurin-17"
        for jdk in (self.jdk, self.other):
            for tool in ("java", "javac"):
                write_executable(jdk / "bin" / tool, 'echo "openjdk version \\"21.0.9\\"" >&2\n')
        self.first = self.tmp / "first"
        self.first.mkdir()
        (self.first / "javac").symlink_to(self.other / "bin/javac")
        # Only what install.sh runs, so the host's own JDK and dpkg (this repo's
        # sessions run on the image) can't stand in for the fakes.
        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        for tool in ("dirname", "grep", "sed", "tee", "head"):
            (self.tools / tool).symlink_to(shutil.which(tool))

    def install(self, **env):
        # The helpers as install-toolset.sh exports them, with the fakes' PATH.
        script = (f'source "{INSTALL_TOOLSET}"; export -f log die package_list; '
                  f'PATH="{self.first}:{self.bin}:{self.tools}" exec "$BASH" "{TOOLSETS}/jvm/install.sh"')
        settings = {"TOOLSET_RECORD_DIR": str(self.record), "TOOLSET_LOG_NAME": "jvm",
                    "FAKE_JDK": str(self.jdk), "FAKE_JDK_PACKAGE": self.PACKAGES[0], **env}
        return subprocess.run(["bash", "-c", script], env=base_env(self.tmp, self.bin, **settings),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)

    def java_home(self, **env):
        result = subprocess.run(["/bin/sh", "-c", f'. "{self.record}/jvm.profile.sh"; printf %s "$JAVA_HOME"'],
                                env={"PATH": "/usr/bin:/bin", **env}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_records_the_packages_jdk_not_the_first_javac(self):
        self.assertEqual(len(self.PACKAGES), 1, "jvm/packages.txt names one JDK")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.java_home(), str(self.jdk))
        self.assertEqual((self.record / "jvm.path").read_text(), f"{self.jdk}/bin\n")
        self.assertIn(f"JAVA_HOME {self.jdk}", result.stdout)

    def test_a_sessions_own_java_home_wins(self):
        # The cloud images source the profile again in every bash (BASH_ENV).
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.java_home(JAVA_HOME="/opt/other-jdk"), "/opt/other-jdk")

    def test_a_package_that_isnt_installed_fails_the_install(self):
        result = self.install(FAKE_JDK_PACKAGE="some-other-jdk")
        self.assertEqual(result.returncode, 1)
        self.assertIn("dpkg can't list", result.stdout + result.stderr)
        self.assertFalse((self.record / "jvm.profile.sh").exists())

    def test_a_package_without_javac_fails_the_install(self):
        result = self.install(FAKE_JDK_NO_JAVAC="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected one bin/javac", result.stdout + result.stderr)
        self.assertFalse((self.record / "jvm.profile.sh").exists())


class ToolsetEnvTests(unittest.TestCase):
    """A toolset's install.sh reads another toolset's record with toolset_env."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.toolsets = self.tmp / "toolsets"
        self.toolsets.mkdir()
        shutil.copy(INSTALL_TOOLSET, self.toolsets / "install-toolset.sh")
        self.record = self.tmp / "record"
        self.bin = self.tmp / "bin"
        write_executable(self.bin / "sudo", FAKE_SUDO)
        base = self.toolsets / "base"
        base.mkdir()
        (base / "path.txt").write_text("# its tools\n/opt/base/bin\n\n/opt/base/sbin\n")
        (base / "profile.sh").write_text("export BASE_HOME=/opt/base\n")

    def toolset(self, name, install):
        directory = self.toolsets / name
        directory.mkdir()
        (directory / "install.sh").write_text("set -euo pipefail\n" + install)

    def install(self, *names):
        return subprocess.run(["bash", str(self.toolsets / "install-toolset.sh"), *names],
                              env=base_env(self.tmp, self.bin, AGENT_TOOLSETS_DIR=str(self.record),
                                           AGENT_BREW=str(self.tmp / "no-brew")),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)

    def test_puts_the_recorded_paths_in_front_and_sources_the_profile(self):
        self.toolset("user", 'toolset_env base\nlog "$BASE_HOME ${PATH%%:' + str(self.bin) + '*}"\n')
        result = self.install("base", "user")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("==> user: /opt/base /opt/base/bin:/opt/base/sbin", result.stdout)

    def test_profiles_add_to_path_as_at_a_sessions_start(self):
        # The go toolset's own profile, which adds its install bin only while PATH
        # is being set up.
        go = self.toolsets / "go"
        go.mkdir()
        shutil.copy(TOOLSETS / "go/profile.sh", go / "profile.sh")
        self.toolset("user", 'toolset_env go\nlog "${PATH##*:}"\n')
        result = self.install("go", "user")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"==> user: {self.tmp}/go/bin", result.stdout)

    def test_paths_go_in_the_records_order_as_in_a_session(self):
        # toolset-profile.sh reads *.path in the record's file order, not install order.
        other = self.toolsets / "another"
        other.mkdir()
        (other / "path.txt").write_text("/opt/another/bin\n")
        self.toolset("user", 'toolset_env base another\nlog "${PATH%%:' + str(self.bin) + '*}"\n')
        result = self.install("base", "another", "user")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("==> user: /opt/another/bin:/opt/base/bin:/opt/base/sbin", result.stdout)

    def test_a_toolset_not_installed_yet_is_refused(self):
        self.toolset("user", "toolset_env base\n")
        result = self.install("user")
        self.assertEqual(result.returncode, 1)
        self.assertIn("toolset_env: base isn't installed", result.stderr)

    def test_the_toolset_installing_reads_what_it_recorded_so_far(self):
        self.toolset("self", 'echo "export SELF_HOME=/opt/self" >"$TOOLSET_RECORD_DIR/self.profile.sh"\n'
                             'toolset_env self\nlog "$SELF_HOME"\n')
        result = self.install("self")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("==> self: /opt/self", result.stdout)


class PromiseTests(unittest.TestCase):
    """Read over every toolset."""

    @staticmethod
    def exported(name):
        """What a toolset records: its profile.sh's exports, and those its install.sh
        writes to its <name>.profile.sh record."""
        variables = set()
        directory = TOOLSETS / name
        profile = directory / "profile.sh"
        if profile.exists():
            variables |= set(re.findall(r"^\s*export\s+([A-Z_][A-Z0-9_]*)=", profile.read_text(), re.M))
        install = directory / "install.sh"
        if install.exists() and f"{name}.profile.sh" in install.read_text():
            variables |= set(re.findall(r"export ([A-Z_][A-Z0-9_]*)=", install.read_text()))
        return variables

    def test_every_variable_a_claude_md_promises_is_recorded(self):
        recorded = {}
        for directory in toolset_dirs():
            for variable in self.exported(directory.name):
                recorded.setdefault(variable, directory.name)
        promised = 0
        for directory in toolset_dirs():
            claude_md = directory / "CLAUDE.md"
            if not claude_md.exists():
                continue
            for variable in set(re.findall(r"`\$\{?([A-Z_][A-Z0-9_]*)", claude_md.read_text())):
                promised += 1
                with self.subTest(toolset=directory.name, variable=variable):
                    self.assertIn(variable, recorded, f"{directory.name}/CLAUDE.md promises ${variable}, "
                                                      "but no toolset's profile sets it")
        self.assertGreater(promised, 0)

    def test_the_jvm_toolset_records_java_home(self):
        self.assertIn("JAVA_HOME", self.exported("jvm"))

    def test_no_toolset_reads_an_images_profile(self):
        # A toolset reads another's record (toolset_env), never an image's
        # profile, which states nothing of the toolsets' own.
        for directory in toolset_dirs():
            for script in directory.glob("*.sh"):
                with self.subTest(script=str(script.relative_to(REPO))):
                    self.assertNotRegex(script.read_text(), r"/etc/profile\.d|agent-images\.sh")


if __name__ == "__main__":
    unittest.main()
