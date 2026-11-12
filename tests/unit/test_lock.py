"""scripts/cloud/lock.sh: resolving a type's lock from upstream, and reading one.

resolve runs against a copy of the repo with fakes for docker (bake --print is
the real one), curl and brew. Each fake accepts only the calls lock.sh makes and
fails the way the real tool does on anything else (AGENTS.md, "Test fakes")."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO

SCRIPT = REPO / "scripts/cloud/lock.sh"
REAL_DOCKER = shutil.which("docker")

FAKE_DOCKER = r'''#!/usr/bin/env python3
import hashlib, json, os, subprocess, sys
args = sys.argv[1:]
def log(line):
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(line + "\n")
if args[:3] == ["buildx", "bake", "-f"] and args[4:5] == ["--print"]:
    os.execv(os.environ["REAL_DOCKER"], ["docker"] + args)
if args[:3] == ["buildx", "imagetools", "inspect"] and args[4:] == ["--format", "{{json .Manifest}}"]:
    ref = args[3]
    if ref in json.loads(os.environ.get("FAKE_MISSING_IMAGES", "[]")):
        log("missing " + ref)
        sys.exit("ERROR: " + ref + ": not found")
    # The first FAKE_RATE_LIMITED reads fail as mirror.gcr.io's did in lock-watch
    # run 43141207783.
    limited = sum(line.startswith("rate-limited ") for line in open(os.environ["FAKE_LOG"]).read().splitlines()) \
        if os.path.exists(os.environ["FAKE_LOG"]) else 0
    if limited < int(os.environ.get("FAKE_RATE_LIMITED", "0")):
        log("rate-limited " + ref)
        sys.exit("ERROR: failed to copy: httpReadSeeker: failed open: unexpected status from GET request to "
                 "https://mirror.gcr.io/v2/library/ubuntu/manifests/sha256:d6a959d9: 429 Too Many Requests\n"
                 "toomanyrequests: Quota exceeded for quota metric 'Read requests to external upstream hosts "
                 "per organization per host in the US multi-region'")
    log("digest " + ref)
    digest = hashlib.sha256((ref + os.environ.get("FAKE_DIGEST_SALT", "")).encode()).hexdigest()
    print(json.dumps({"mediaType": "application/vnd.oci.image.index.v1+json", "digest": "sha256:" + digest}))
    sys.exit(0)
if args[:4] == ["run", "--rm", "--entrypoint", "/bin/bash"]:
    rest, mounts, env = args[4:], [], []
    while rest and rest[0] in ("--mount", "-e"):
        (mounts if rest[0] == "--mount" else env).append(rest[1]); rest = rest[2:]
    if len(rest) != 3 or rest[1] != "-c":
        sys.exit("docker: fake: unexpected run: " + " ".join(args))
    for mount in mounts:
        fields = dict(f.partition("=")[::2] for f in mount.split(","))
        if fields.get("type") != "bind" or not fields.get("target"):
            sys.exit("docker: invalid mount config: " + mount)
        # Like docker's --mount (not -v): a missing source is an error.
        if not os.path.isabs(fields.get("source", "")) or not os.path.exists(fields["source"]):
            sys.exit("docker: Error response from daemon: invalid mount config for type \"bind\": bind source path does not exist: " + fields.get("source", ""))
    packages = next((e for e in env if e.startswith("PACKAGES=")), None)
    if packages is None:
        sys.exit("docker: fake: no PACKAGES")
    log("apt " + rest[0] + " " + packages)
    for mount in mounts:
        log("mount " + mount)
    for e in env:
        if not e.startswith("PACKAGES="):
            log("env " + e)
    if os.environ.get("FAKE_APT_FAIL"):
        sys.exit("E: Unable to locate package nosuchthing")
    sys.stdout.write(open(os.environ["FAKE_APT"]).read())
    sys.exit(0)
sys.exit("docker: fake: unexpected call: " + " ".join(args) + "\nSee 'docker --help'.")
'''

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
urls = json.load(open(os.environ["FAKE_URLS"]))
def log(line):
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(line + "\n")
if args[:3] == ["-fsSL", "--retry", "3"] and len(args) == 4:
    url = args[3]
    if url not in urls:
        sys.exit("curl: (22) The requested URL returned error: 404")
    log("get " + url)
    # Like curl: a reader that closes the pipe early fails the transfer (23).
    try:
        sys.stdout.write(urls[url])
        sys.stdout.flush()
    except BrokenPipeError:
        sys.stderr.write("curl: (23) Failure writing output to destination\n")
        os._exit(23)
    sys.exit(0)
if args[:7] == ["-fsSLI", "--retry", "3", "-o", "/dev/null", "-w", "%{url_effective}"] and len(args) == 8:
    url = args[7]
    if "redirect " + url not in urls:
        sys.exit("curl: (22) The requested URL returned error: 404")
    log("head " + url)
    sys.stdout.write(urls["redirect " + url])
    sys.exit(0)
sys.exit("curl: fake: option not expected: " + " ".join(args))
'''

FAKE_BREW = r'''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
data = json.load(open(os.environ["FAKE_BREW_DATA"]))
def log(line):
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(line + "\n")
if args == ["update", "--quiet"]:
    log("brew update"); sys.exit(0)
if len(args) == 2 and args[0] == "tap":
    log("tap " + args[1]); sys.exit(0)
if len(args) == 3 and args[:2] == ["trust", "--tap"]:
    log("trust " + args[2]); sys.exit(0)
if args[:3] == ["bundle", "list", "--formula"] and len(args) == 4 and args[3].startswith("--file="):
    for line in open(args[3][len("--file="):]):
        m = re.match(r'brew "([^"]+)"(.*)', line)
        if m and "OS.mac?" not in m.group(2):
            print(m.group(1))
    sys.exit(0)
if args[:3] == ["deps", "--union", "--full-name"] and len(args) > 3:
    deps = set()
    for name in args[3:]:
        if name not in data:
            sys.exit("Error: No available formula with the name \"" + name + "\".")
        deps.update(data[name]["deps"])
    print("\n".join(sorted(deps)))
    sys.exit(0)
if args[:3] == ["info", "--json=v2", "--formula"] and len(args) > 3:
    out = []
    for name in args[3:]:
        if name not in data:
            sys.exit("Error: No available formula with the name \"" + name + "\".")
        out.append({"full_name": name, "versions": {"stable": data[name]["version"]}, "revision": data[name].get("revision", 0)})
    print(json.dumps({"formulae": out, "casks": []}))
    sys.exit(0)
sys.exit("Error: Unknown command: brew " + " ".join(args))
'''

# Real channel manifests run to megabytes past the version line, which is what
# exposed a reader that stops early (the fake curl then fails, as curl does).
RUST_MANIFEST = ('[pkg.cargo]\nversion = "0.100.0 (abc 2026-10-01)"\n\n[pkg.rust]\nversion = "1.99.1 (58cf8f9c3 2026-10-13)"\n'
                 + '[pkg.rust.target.x86_64-unknown-linux-gnu]\nurl = "https://static.rust-lang.org/x"\n' * 20000)
RUST_191_MANIFEST = '[pkg.rust]\nversion = "1.91.1 (aaaaaaaaa 2025-11-10)"\n'
TERRAFORM_INDEX = json.dumps({"name": "terraform", "versions": {
    v: {} for v in ("1.14.2", "1.14.9", "1.17.0", "1.17.1", "1.18.0-beta1", "1.9.8")
}})

APT_TODAY = (
    "sudo (1.9.15p5-3ubuntu5.24.04.1 Ubuntu:24.04/noble-updates [amd64])\n"
    "libsqlite3-0 [3.45.1-1ubuntu2.5] (3.45.1-1ubuntu2.9 Ubuntu:24.04/noble-updates [amd64])\n"
    "cmake (3.28.3-1build7 Ubuntu:24.04/noble [amd64])\n"
    "ca-certificates [20240203] (20260816ubuntu0.24.04.2 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [all])\n"
)
APT_LINES = [
    "apt ca-certificates 20260816ubuntu0.24.04.2",
    "apt cmake 3.28.3-1build7",
    "apt libsqlite3-0 3.45.1-1ubuntu2.9",
    "apt sudo 1.9.15p5-3ubuntu5.24.04.1",
]


def executable(path: Path, text: str):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def digest_of(ref: str, salt: str = "") -> str:
    return "sha256:" + hashlib.sha256((ref + salt).encode()).hexdigest()


@unittest.skipUnless(REAL_DOCKER, "needs docker buildx for bake --print")
class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        for name in ("images/cloud", "images/toolsets", "infra/gcp", "scripts/cloud", "hooks/gcp"):
            shutil.copytree(REPO / name, self.repo / name)
        self.locks = self.tmp / "locks"
        self.locks.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        executable(self.bin / "docker", FAKE_DOCKER)
        executable(self.bin / "curl", FAKE_CURL)
        executable(self.bin / "brew", FAKE_BREW)
        self.log = self.tmp / "log"
        self.apt = self.tmp / "apt.txt"
        self.apt.write_text(APT_TODAY)
        self.urls = {
            "https://downloads.claude.ai/claude-code-releases/latest": "2.1.319",
            "https://downloads.claude.ai/claude-code-releases/stable": "2.1.311",
            "https://static.rust-lang.org/dist/channel-rust-stable.toml": RUST_MANIFEST,
            "https://static.rust-lang.org/dist/channel-rust-1.91.toml": RUST_191_MANIFEST,
            "https://releases.hashicorp.com/terraform/index.json": TERRAFORM_INDEX,
            "redirect https://github.com/terraform-linters/tflint/releases/latest":
                "https://github.com/terraform-linters/tflint/releases/tag/v0.65.0",
            "redirect https://github.com/VirtualGL/virtualgl/releases/latest":
                "https://github.com/VirtualGL/virtualgl/releases/tag/3.1.5",
        }
        # Every formula the real Brewfiles name for linux, gis and linux-gpu, plus
        # dependencies; a formula missing here fails the way brew does.
        brew = {}
        for brewfile in (self.repo / "images/toolsets").glob("*/Brewfile"):
            for line in brewfile.read_text().splitlines():
                if line.startswith('brew "') and "OS.mac?" not in line:
                    brew[line.split('"')[1]] = {"version": "1.0", "deps": []}
        brew["go"] = {"version": "1.27.3", "deps": []}
        brew["node"] = {"version": "26.1.0", "deps": ["openssl@3", "icu4c@77"]}
        brew["python"] = {"version": "3.14.0", "revision": 1, "deps": ["openssl@3"]}
        brew["openssl@3"] = {"version": "3.6.0", "deps": []}
        brew["icu4c@77"] = {"version": "77.1", "deps": []}
        self.brew = brew

    def run_lock(self, *args, **env):
        (self.tmp / "urls.json").write_text(json.dumps(self.urls))
        (self.tmp / "brew.json").write_text(json.dumps(self.brew))
        settings = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "REAL_DOCKER": REAL_DOCKER,
            "FAKE_LOG": str(self.log),
            "FAKE_APT": str(self.apt),
            "FAKE_URLS": str(self.tmp / "urls.json"),
            "FAKE_BREW_DATA": str(self.tmp / "brew.json"),
            "LOCK_DIR": str(self.locks),
            "LOCK_NOW": "20261106T220000Z",
            "LOCK_REGISTRY_WAITS": "0 0 0",
        }
        settings.update(env)
        return subprocess.run(
            ["bash", str(self.repo / "scripts/cloud/lock.sh"), *args],
            cwd=self.repo, env=settings, capture_output=True, text=True, timeout=60,
        )

    def resolve(self, type_="linux", **env):
        result = self.run_lock("resolve", type_, **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def entries(self, lock):
        return [line for line in lock.splitlines() if not line.startswith("#")]

    def write_lock(self, type_, text):
        (self.locks / type_).mkdir(exist_ok=True)
        (self.locks / type_ / "versions.lock").write_text(text)

    def edit(self, name, old, new):
        path = self.repo / name
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def test_linux_resolves_every_dependency(self):
        lock = self.resolve()
        self.assertTrue(lock.startswith(f"# {self.locks}/linux/versions.lock: what agent-linux is built from"))
        entries = self.entries(lock)
        self.assertEqual(entries, sorted(entries, key=lambda line: line.encode()))
        ubuntu = "mirror.gcr.io/library/ubuntu:24.04"
        for line in [
            f"base - {ubuntu}@{digest_of(ubuntu)}",
            "apt-snapshot - 20261106T220000Z",
            "claude - 2.1.319",
            "rust - 1.99.1",
            "terraform - 1.17.1",
            "release tflint v0.65.0",
            "brew go 1.27.3",
            "brew python 3.14.0_1",
            "brew openssl@3 3.6.0",
            "brew lolay/tap/nowline 1.0",
            *APT_LINES,
        ]:
            self.assertIn(line, entries)
        self.assertFalse([line for line in entries if line.startswith(("pinned", "apt-floating"))])
        self.assertNotIn("release virtualgl", lock)
        self.assertNotIn("brew git", lock, "a Brewfile's `if OS.mac?` line is left out on Linux")

    def test_apt_resolves_the_base_list_and_every_toolsets_in_the_locked_base(self):
        self.resolve()
        apt = next(line for line in self.log.read_text().splitlines() if line.startswith("apt "))
        ubuntu = "mirror.gcr.io/library/ubuntu:24.04"
        self.assertTrue(apt.startswith(f"apt {ubuntu}@{digest_of(ubuntu)} PACKAGES=sudo ca-certificates curl "), apt)
        self.assertIn(" openjdk-21-jdk-headless ", apt)
        self.assertIn(" xvfb ", apt)

    def test_apt_resolves_with_the_build_apt_settings(self):
        # A part of the archive that fails to download has to fail the resolve,
        # not lock older versions resolved without it (apt-build.conf).
        self.resolve()
        log = self.log.read_text().splitlines()
        conf = self.repo / "images/cloud/base/apt-build.conf"
        self.assertIn(f"mount type=bind,source={conf},target=/tmp/apt-build.conf,readonly", log)
        self.assertIn("env APT_CONFIG=/tmp/apt-build.conf", log)
        self.assertIn('\nAPT::Update::Error-Mode "any";\n', conf.read_text())

    def test_taps_are_trusted_before_resolving(self):
        self.resolve()
        log = self.log.read_text().splitlines()
        self.assertLess(log.index("tap lolay/tap"), log.index("trust lolay/tap"))

    def test_gpu_type_takes_the_cuda_base_and_virtualgl(self):
        entries = self.entries(self.resolve("linux-gpu"))
        cuda = "mirror.gcr.io/nvidia/cuda:13.0.3-devel-ubuntu24.04"
        self.assertIn(f"base - {cuda}@{digest_of(cuda)}", entries)
        self.assertIn("release virtualgl 3.1.5", entries)

    def test_gis_has_no_rust_or_terraform(self):
        lock = self.resolve("gis")
        self.assertNotIn("\nrust ", lock)
        self.assertNotIn("\nterraform ", lock)
        self.assertNotIn("tflint", lock)

    def test_snapshot_stays_when_apt_resolves_what_the_lock_has(self):
        first = self.resolve()
        self.write_lock("linux", first.replace("20261106T220000Z", "20261101T000000Z"))
        again = self.resolve(LOCK_NOW="20261107T000000Z")
        self.assertIn("apt-snapshot - 20261101T000000Z", again)

    def test_snapshot_moves_when_apt_moved(self):
        self.write_lock("linux", self.resolve().replace("20261106T220000Z", "20261101T000000Z"))
        self.apt.write_text(APT_TODAY.replace("3.45.1-1ubuntu2.9", "3.45.1-1ubuntu2.10"))
        again = self.resolve(LOCK_NOW="20261107T000000Z")
        self.assertIn("apt-snapshot - 20261107T000000Z", again)
        self.assertIn("apt libsqlite3-0 3.45.1-1ubuntu2.10", again)

    def test_snapshot_moves_when_the_base_moved(self):
        self.write_lock("linux", self.resolve().replace("20261106T220000Z", "20261101T000000Z"))
        again = self.resolve(LOCK_NOW="20261107T000000Z", FAKE_DIGEST_SALT="new")
        self.assertIn("apt-snapshot - 20261107T000000Z", again)

    def test_a_source_with_no_snapshot_is_floating(self):
        self.apt.write_text(APT_TODAY + "libnvjitlink-13-0 (13.0.88-1 cuda-ubuntu2404-x86_64 [amd64])\n")
        entries = self.entries(self.resolve())
        self.assertIn("apt-floating libnvjitlink-13-0 13.0.88-1", entries)

    def test_a_pinned_rust_skips_the_channel(self):
        self.edit("images/toolsets/rust/install.sh", 'toolchain="${RUST_TOOLCHAIN:-stable}"',
                  'toolchain="${RUST_TOOLCHAIN:-1.91.0}" # pinned: https://github.com/rust-lang/rust/issues/1')
        entries = self.entries(self.resolve())
        self.assertIn("rust - 1.91.0", entries)
        self.assertIn("pinned rust https://github.com/rust-lang/rust/issues/1", entries)
        self.assertNotIn("channel-rust", self.log.read_text())

    def test_a_minor_line_takes_its_newest_patch(self):
        self.edit("images/toolsets/rust/install.sh", "${RUST_TOOLCHAIN:-stable}", "${RUST_TOOLCHAIN:-1.91}")
        entries = self.entries(self.resolve())
        self.assertIn("rust - 1.91.1", entries)
        self.assertIn("pinned rust -", entries)

    def test_terraform_constraint_takes_the_newest_stable_on_its_line(self):
        self.edit("images/toolsets/terraform/install.sh", "${TERRAFORM_VERSION:-latest}", "${TERRAFORM_VERSION:-~> 1.14}")
        self.assertIn("terraform - 1.14.9", self.entries(self.resolve()))

    def test_a_pinned_claude_skips_the_channel(self):
        self.edit("images/cloud/docker-bake.hcl", 'default = "latest"', 'default = "2.1.300"')
        entries = self.entries(self.resolve())
        self.assertIn("claude - 2.1.300", entries)
        self.assertNotIn("claude-code-releases", self.log.read_text())

    def test_a_pinned_base_keeps_its_digest(self):
        pinned = "mirror.gcr.io/library/ubuntu:24.04@sha256:" + "a" * 64
        self.edit("images/cloud/docker-bake.hcl", 'BASE_IMAGE = "mirror.gcr.io/library/ubuntu:24.04"',
                  f'BASE_IMAGE = "{pinned}"')
        entries = self.entries(self.resolve())
        self.assertIn(f"base - {pinned}", entries)
        self.assertIn("pinned base -", entries)

    def test_an_apt_pin_is_listed(self):
        self.edit("images/toolsets/native/packages.txt", "cmake\n", "cmake=3.28.3-1build7\n")
        self.assertIn("pinned apt/cmake -", self.entries(self.resolve()))

    def test_failures_fail_the_resolve(self):
        cases = {
            "bad claude channel": ({"https://downloads.claude.ai/claude-code-releases/latest": "<html>"}, {}),
            "no claude channel": ({"https://downloads.claude.ai/claude-code-releases/latest": None}, {}),
            "no tflint release": ({"redirect https://github.com/terraform-linters/tflint/releases/latest": None}, {}),
            # A repo with no releases redirects /releases/latest to /releases.
            "tflint redirect to no tag": ({"redirect https://github.com/terraform-linters/tflint/releases/latest":
                                           "https://github.com/terraform-linters/tflint/releases"}, {}),
            "apt fails": ({}, {"FAKE_APT_FAIL": "1"}),
            "no base image": ({}, {"FAKE_MISSING_IMAGES": json.dumps(["mirror.gcr.io/library/ubuntu:24.04"])}),
        }
        for name, (urls, env) in cases.items():
            with self.subTest(name):
                saved = dict(self.urls)
                for url, body in urls.items():
                    if body is None:
                        self.urls.pop(url)
                    else:
                        self.urls[url] = body
                result = self.run_lock("resolve", "linux", **env)
                self.urls = saved
                self.assertNotEqual(result.returncode, 0, name)
                self.assertEqual(result.stdout, "", f"{name}: printed a partial lock")

    def log_lines(self, prefix):
        return [line for line in self.log.read_text().splitlines() if line.startswith(prefix)]

    def test_a_rate_limited_registry_is_retried(self):
        lock = self.resolve(FAKE_RATE_LIMITED="2")
        ubuntu = "mirror.gcr.io/library/ubuntu:24.04"
        self.assertIn(f"base - {ubuntu}@{digest_of(ubuntu)}", self.entries(lock))
        self.assertEqual(len(self.log_lines("rate-limited ")), 2)

    def test_a_registry_still_rate_limited_after_every_wait_fails(self):
        result = self.run_lock("resolve", "linux", FAKE_RATE_LIMITED="99", LOCK_REGISTRY_WAITS="0 0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(self.log_lines("rate-limited ")), 3, "one read, then one after each wait")
        self.assertIn("429 Too Many Requests", result.stderr)
        self.assertIn("lock: couldn't read mirror.gcr.io/library/ubuntu:24.04's digest", result.stderr)

    def test_other_registry_failures_are_not_retried(self):
        result = self.run_lock("resolve", "linux",
                               FAKE_MISSING_IMAGES=json.dumps(["mirror.gcr.io/library/ubuntu:24.04"]))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.log_lines("missing ")), 1)
        self.assertIn("lock: couldn't read mirror.gcr.io/library/ubuntu:24.04's digest", result.stderr)

    def test_an_unknown_formula_fails(self):
        del self.brew["go"]
        result = self.run_lock("resolve", "linux")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_only_cloud_run_types(self):
        for type_ in ("android", "nope"):
            with self.subTest(type_):
                result = self.run_lock("resolve", type_)
                self.assertEqual(result.returncode, 1)
                self.assertIn("isn't a Cloud Run type", result.stderr)


class ReadTests(unittest.TestCase):
    LOCK = (
        "# a header\n"
        "apt cmake 3.28.3-1build7\n"
        "apt sqlite3 3.45.1-1ubuntu2.8\n"
        "apt-snapshot - 20261101T000000Z\n"
        "base - mirror.gcr.io/library/ubuntu:24.04@sha256:aaa\n"
        "brew go 1.27.3\n"
        "brew lolay/tap/nowline 0.4.0\n"
        "claude - 2.1.318\n"
        "release tflint v0.64.0\n"
        "rust - 1.99.1\n"
        "terraform - 1.17.1\n"
    )

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "linux").mkdir()

    def run_lock(self, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), *args], cwd=REPO, capture_output=True, text=True, timeout=30,
            env={**os.environ, "LOCK_DIR": str(self.tmp)},
        )

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text)
        return str(path)

    def test_bake_args_sets_each_pinned_input(self):
        self.write("linux/versions.lock", self.LOCK + "release virtualgl 3.1.5\n")
        result = self.run_lock("bake-args", "linux")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(result.stdout.splitlines()), [
            "*.args.APT_SNAPSHOT=20261101T000000Z",
            "*.args.BASE_IMAGE=mirror.gcr.io/library/ubuntu:24.04@sha256:aaa",
            "*.args.CLAUDE_VERSION=2.1.318",
            "*.args.RUST_TOOLCHAIN=1.99.1",
            "*.args.TERRAFORM_VERSION=1.17.1",
            "*.args.TFLINT_VERSION=v0.64.0",
            "*.args.VIRTUALGL_VERSION=3.1.5",
        ])

    def test_bake_args_refuses_a_missing_or_incomplete_lock(self):
        result = self.run_lock("bake-args", "linux")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no lock for linux", result.stderr)
        for kind in ("base", "apt-snapshot", "claude"):
            with self.subTest(kind):
                lines = [line for line in self.LOCK.splitlines() if not line.startswith(kind + " ")]
                self.write("linux/versions.lock", "\n".join(lines) + "\n")
                result = self.run_lock("bake-args", "linux")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"has no {kind} line", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_bake_args_refuses_an_unknown_kind(self):
        self.write("linux/versions.lock", self.LOCK + "npm left-pad 1.3.0\n")
        result = self.run_lock("bake-args", "linux")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown line kind 'npm'", result.stderr)

    def test_diff_and_summary(self):
        old = self.write("old", self.LOCK)
        new = self.write("new", self.LOCK
                         .replace("2.1.318", "2.1.319")
                         .replace("1ubuntu2.8", "1ubuntu2.9")
                         .replace("20261101T000000Z", "20261106T220000Z")
                         .replace("nowline 0.4.0", "nowline 0.5.0")
                         .replace("brew go 1.27.3\n", "")
                         + "release virtualgl 3.1.5\n")
        diff = self.run_lock("diff", old, new).stdout.splitlines()
        self.assertEqual(diff, [
            "apt-snapshot/-: 20261101T000000Z -> 20261106T220000Z",
            "apt/sqlite3: 3.45.1-1ubuntu2.8 -> 3.45.1-1ubuntu2.9",
            "brew/go: 1.27.3 -> (none)",
            "brew/lolay/tap/nowline: 0.4.0 -> 0.5.0",
            "claude/-: 2.1.318 -> 2.1.319",
            "release/virtualgl: (none) -> 3.1.5",
        ])
        self.assertEqual(
            self.run_lock("summary", old, new).stdout.strip(),
            "claude 2.1.319, virtualgl 3.1.5, go removed, nowline 0.5.0, apt 1 package",
        )

    def test_an_unchanged_lock_has_no_diff(self):
        old = self.write("old", self.LOCK)
        self.assertEqual(self.run_lock("diff", old, old).stdout, "")
        self.assertEqual(self.run_lock("summary", old, old).stdout.strip(), "")

    def test_usage(self):
        for args in ([], ["resolve"], ["bake-args"], ["diff", "a"], ["nope", "x"]):
            with self.subTest(args):
                self.assertEqual(self.run_lock(*args).returncode, 1)


if __name__ == "__main__":
    unittest.main()
