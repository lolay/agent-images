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
    # The archive apt reads: today's index (what FAKE_APT's simulation installs),
    # plus each snapshot apt-pins.sh adds as a source, when the container has the
    # script and the pins. A snapshot's index lists each pocket's current version
    # only, so a name=version in neither is refused, as apt refuses it.
    today = open(os.environ["FAKE_APT"]).read().splitlines()
    sources = [today]
    pins = next((e[len("APT_PINS="):] for e in env if e.startswith("APT_PINS=")), "")
    if pins and any(m.endswith("target=/tmp/apt-pins.sh,readonly") for m in mounts):
        snapshots = json.loads(os.environ.get("FAKE_APT_SNAPSHOTS", "{}"))
        for pin in pins.split():
            name, _, snapshot = pin.rpartition("@")
            if "=" not in name or len(snapshot) != 16:
                sys.exit("apt-pins: '" + pin + "' isn't <name>=<version>@<snapshot>")
            if snapshot not in snapshots:
                sys.exit("E: Failed to fetch https://snapshot.ubuntu.com/ubuntu/" + snapshot + "/dists/noble/InRelease  404  Not Found")
            sources.append(snapshots[snapshot])
    def version(line):
        return line.split("(", 1)[1].split(" ", 1)[0]
    # What the base image already has: apt installs nothing for it, so no Inst.
    installed = json.loads(os.environ.get("FAKE_APT_INSTALLED", "{}"))
    out = list(today)
    for wanted in packages[len("PACKAGES="):].split():
        name, _, want = wanted.partition("=")
        if not want:
            continue
        if installed.get(name) == want:
            out = [line for line in out if line.split(" ", 1)[0] != name]
            continue
        found = next((line for lines in sources for line in lines
                      if line.split(" ", 1)[0] == name and version(line) == want), None)
        if found is None:
            sys.stderr.write("E: Version '" + want + "' for '" + name + "' was not found\n")
            sys.exit(100)
        out = [line for line in out if line.split(" ", 1)[0] != name] + [found]
    sys.stdout.write("".join(line + "\n" for line in out))
    sys.exit(0)
sys.exit("docker: fake: unexpected call: " + " ".join(args) + "\nSee 'docker --help'.")
'''

FAKE_CURL = r'''#!/usr/bin/env python3
# curl for the URLs in FAKE_URLS: "<url>" is a body, "redirect <url>" where a
# HEAD with -w %{url_effective} lands, and "status <url>" the HTTP errors its
# first attempts get, one each. A status of "partial" sends "partial <url>" and
# then breaks off (curl exits 56), as a dropped connection does: written to
# stdout, those bytes stay; with -o, curl starts the file afresh each attempt.
# Retries follow curl's rules: --retry N retries a transient error (408, 429,
# 5xx) up to N times, and anything else only with --retry-all-errors, so a 403
# without it fails at once (curl exits 22).
import json, os, sys
args = sys.argv[1:]
urls = json.load(open(os.environ["FAKE_URLS"]))
def log(line):
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(line + "\n")
def attempts(url):
    if not os.path.exists(os.environ["FAKE_LOG"]):
        return 0
    return sum(line == "attempt " + url for line in open(os.environ["FAKE_LOG"]).read().splitlines())
flags, retry, out, write, url, i = set(), 0, None, None, None, 0
while i < len(args):
    a = args[i]
    if a in ("-fsSL", "-fsSLI", "--retry-all-errors"):
        flags.add(a); i += 1
    elif a == "--retry":
        retry = int(args[i + 1]); i += 2
    elif a == "-o":
        out = args[i + 1]; i += 2
    elif a == "-w" and args[i + 1] == "%{url_effective}":
        write = args[i + 1]; i += 2
    elif a.startswith("-"):
        sys.exit("curl: option " + a + ": is unknown\ncurl: try 'curl --help' for more information")
    elif url is None:
        url = a; i += 1
    else:
        sys.exit("curl: fake: a second URL: " + a)
if url is None or not flags & {"-fsSL", "-fsSLI"}:
    sys.exit("curl: fake: option not expected: " + " ".join(args))
head = "-fsSLI" in flags
if head and (out != "/dev/null" or write is None):
    sys.exit("curl: fake: option not expected: " + " ".join(args))
key = ("redirect " + url) if head else url
for tries in range(retry + 1):
    n = attempts(url)
    log("attempt " + url)
    statuses = urls.get("status " + url, [])
    status = statuses[n] if n < len(statuses) else (200 if key in urls else 404)
    if status == 200:
        break
    if status == "partial":
        if out is not None:
            open(out, "w").write(urls["partial " + url])
        else:
            sys.stdout.write(urls["partial " + url]); sys.stdout.flush()
        if tries == retry or "--retry-all-errors" not in flags:
            sys.exit("curl: (56) Recv failure: Connection reset by peer")
        continue
    transient = status in (408, 429, 500, 502, 503, 504)
    if tries == retry or not (transient or "--retry-all-errors" in flags):
        sys.exit("curl: (22) The requested URL returned error: " + str(status))
if head:
    log("head " + url)
    sys.stdout.write(urls[key])
    sys.exit(0)
log("get " + url)
if out is not None:
    open(out, "w").write(urls[url])
    sys.exit(0)
# Like curl: a reader that closes the pipe early fails the transfer (23).
try:
    sys.stdout.write(urls[url])
    sys.stdout.flush()
except BrokenPipeError:
    sys.stderr.write("curl: (23) Failure writing output to destination\n")
    os._exit(23)
sys.exit(0)
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
        self.apt_snapshots = {}
        self.apt_installed = {}
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
            "redirect https://github.com/colmap/colmap/releases/latest":
                "https://github.com/colmap/colmap/releases/tag/4.2.1",
            # blender.org's index pages: the newest series, then its newest Linux build.
            "https://download.blender.org/release/":
                '<a href="Blender5.2/">Blender5.2/</a>\n<a href="Blender5.3/">Blender5.3/</a>\n'
                '<a href="Blender5.10/">Blender5.10/</a>\n',
            "https://download.blender.org/release/Blender5.10/":
                '<a href="blender-5.10.0-linux-x64.tar.xz">x</a>\n<a href="blender-5.10.1-linux-x64.tar.xz">x</a>\n'
                '<a href="blender-5.10.1-windows-x64.zip">x</a>\n<a href="blender-5.10.1.sha256">x</a>\n',
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
            "FAKE_APT_SNAPSHOTS": json.dumps(self.apt_snapshots),
            "FAKE_APT_INSTALLED": json.dumps(self.apt_installed),
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

    def test_3d_gpu_takes_colmap_and_the_newest_blender(self):
        # Blender's series sort as versions (5.10 after 5.3), not as text.
        entries = self.entries(self.resolve("3d-gpu"))
        for line in ("release colmap 4.2.1", "release blender 5.10.1", "release virtualgl 3.1.5"):
            self.assertIn(line, entries)
        self.assertFalse([line for line in entries if line.startswith(("rust ", "terraform "))])

    def test_3d_gpu_resolves_its_builder_packages(self):
        # COLMAP's build list is locked with the rest, so the libraries the image
        # keeps at those versions move the snapshot and take pins (r4.f10).
        self.edit("images/toolsets/3d/build-packages.txt", "libceres-dev\n", "libceres-dev=2.2.0+dfsg-4.1ubuntu1\n")
        self.apt.write_text(APT_TODAY + "libceres-dev (2.2.0+dfsg-4.1ubuntu1 Ubuntu:24.04/noble [amd64])\n")
        entries = self.entries(self.resolve("3d-gpu"))
        apt = next(line for line in self.log.read_text().splitlines() if line.startswith("apt "))
        self.assertIn(" libopenimageio-dev ", apt)
        self.assertIn(" libceres-dev=2.2.0+dfsg-4.1ubuntu1", apt)
        self.assertIn("pinned apt/libceres-dev -", entries)

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
        entries = self.entries(self.resolve())
        self.assertIn("terraform - 1.14.9", entries)
        self.assertIn("pinned terraform -", entries)

    def test_a_pinned_terraform_names_its_issue(self):
        self.edit("images/toolsets/terraform/install.sh", 'terraform="${TERRAFORM_VERSION:-latest}"',
                  'terraform="${TERRAFORM_VERSION:-1.14.2}" # pinned: https://github.com/hashicorp/terraform/issues/3')
        entries = self.entries(self.resolve())
        self.assertIn("terraform - 1.14.2", entries)
        self.assertIn("pinned terraform https://github.com/hashicorp/terraform/issues/3", entries)
        self.assertNotIn("releases.hashicorp.com", self.log.read_text())

    def test_a_pinned_release_tool_skips_its_source_and_names_its_issue(self):
        # The pin's note on the line above the constraint, which pin_note reads too.
        self.edit("images/toolsets/3d/build-colmap.sh", 'version="${COLMAP_VERSION:-latest}"',
                  '# pinned: https://github.com/colmap/colmap/issues/7\nversion="${COLMAP_VERSION:-3.12.0}"')
        entries = self.entries(self.resolve("3d-gpu"))
        self.assertIn("release colmap 3.12.0", entries)
        self.assertIn("pinned colmap https://github.com/colmap/colmap/issues/7", entries)
        self.assertNotIn("github.com/colmap/colmap/releases/latest", self.log.read_text())
        self.assertIn("release blender 5.10.1", entries)

    def test_a_pinned_claude_skips_the_channel(self):
        self.edit("images/cloud/docker-bake.hcl", 'default = "latest"', 'default = "2.1.300"')
        entries = self.entries(self.resolve())
        self.assertIn("claude - 2.1.300", entries)
        self.assertIn("pinned claude -", entries)
        self.assertNotIn("claude-code-releases", self.log.read_text())

    def test_a_claude_pin_names_its_issue_anywhere_in_its_variable_block(self):
        # gcp.md: the note goes where the dependency is declared, which for Claude
        # is the `default` inside `variable "CLAUDE_VERSION"` (r3.f16).
        issue = "https://github.com/anthropics/claude-code/issues/9"
        for old, new in (('default = "latest"', f'default = "2.1.300" # pinned: {issue}'),
                         ('variable "CLAUDE_VERSION" {', f'# pinned: {issue}\nvariable "CLAUDE_VERSION" {{'),
                         ('variable "CLAUDE_VERSION" {', f'variable "CLAUDE_VERSION" {{ # pinned: {issue}')):
            with self.subTest(new):
                bake = self.repo / "images/cloud/docker-bake.hcl"
                saved = bake.read_text()
                self.edit("images/cloud/docker-bake.hcl", old, new)
                if "2.1.300" not in new:
                    self.edit("images/cloud/docker-bake.hcl", 'default = "latest"', 'default = "2.1.300"')
                entries = self.entries(self.resolve())
                bake.write_text(saved)
                self.assertIn("claude - 2.1.300", entries)
                self.assertIn(f"pinned claude {issue}", entries)

    def test_a_note_past_the_variable_block_is_not_its_pin(self):
        self.edit("images/cloud/docker-bake.hcl", 'default = "latest"', 'default = "2.1.300"')
        self.edit("images/cloud/docker-bake.hcl", 'variable "COMMIT" {',
                  '# pinned: https://github.com/anthropics/claude-code/issues/9\nvariable "COMMIT" {')
        self.assertIn("pinned claude -", self.entries(self.resolve()))

    def test_claude_version_in_the_environment_doesnt_reach_the_lock(self):
        # bake.sh and the Makefile export CLAUDE_VERSION; the lock reads the
        # bake file's own default, as the watch on main does.
        entries = self.entries(self.resolve(CLAUDE_VERSION="2.1.999"))
        self.assertIn("claude - 2.1.319", entries)
        self.assertNotIn("pinned claude -", entries)

    def test_a_pinned_base_keeps_its_digest(self):
        pinned = "mirror.gcr.io/library/ubuntu:24.04@sha256:" + "a" * 64
        self.edit("images/cloud/docker-bake.hcl", 'BASE_IMAGE = "mirror.gcr.io/library/ubuntu:24.04"',
                  f'BASE_IMAGE = "{pinned}"')
        entries = self.entries(self.resolve())
        self.assertIn(f"base - {pinned}", entries)
        self.assertIn("pinned base -", entries)

    def test_an_apt_pin_is_listed(self):
        self.edit("images/toolsets/native/packages.txt", "cmake\n", "cmake=3.28.3-1build7\n")
        entries = self.entries(self.resolve())
        self.assertIn("pinned apt/cmake -", entries)
        # With no snapshot on the pin, it resolves against today's archive alone.
        self.assertFalse([line for line in entries if line.startswith("apt-pin-snapshot ")])
        self.assertIn("env APT_PINS=", self.log.read_text().splitlines())

    ISSUE = "https://github.com/lolay/agent-images/issues/117"

    def test_an_apt_pin_names_its_issue(self):
        self.edit("images/toolsets/native/packages.txt", "cmake\n", f"cmake=3.28.3-1build7  # pinned: {self.ISSUE}\n")
        entries = self.entries(self.resolve())
        self.assertIn(f"pinned apt/cmake {self.ISSUE}", entries)
        self.assertNotIn("pinned apt/cmake -", entries)

    def test_an_apt_pin_with_a_snapshot_names_its_issue_not_the_snapshot(self):
        self.pin_openssl(f"  # pinned: {self.ISSUE} snapshot={self.JANUARY}")
        entries = self.entries(self.resolve())
        self.assertIn(f"pinned apt/openssl {self.ISSUE}", entries)
        self.assertIn(f"apt-pin-snapshot openssl 3.0.13-0ubuntu3.6@{self.JANUARY}", entries)

    def test_an_apt_pin_with_a_snapshot_and_no_issue_stays_unnamed(self):
        self.pin_openssl(f"  # pinned: snapshot={self.JANUARY}")
        self.assertIn("pinned apt/openssl -", self.entries(self.resolve()))

    def pin_curl_from_january(self):
        self.apt_snapshots = {self.JANUARY: ["curl (8.5.0-2ubuntu10.1 Ubuntu:24.04/noble-updates [amd64])"]}

    def test_the_same_apt_pin_in_two_lists_names_its_issue_once(self):
        self.pin_curl_from_january()
        pin = f"curl=8.5.0-2ubuntu10.1 # pinned: {self.ISSUE} snapshot={self.JANUARY}\n"
        self.edit("images/cloud/base/packages.txt", "curl\n", pin)
        self.edit("images/toolsets/core/packages.txt", "curl\n", pin)
        entries = self.entries(self.resolve())
        self.assertEqual([line for line in entries if line.startswith("pinned apt/curl")],
                         [f"pinned apt/curl {self.ISSUE}"])

    def test_an_apt_pin_with_an_issue_in_one_list_and_none_in_the_other_names_the_issue(self):
        self.pin_curl_from_january()
        self.edit("images/cloud/base/packages.txt", "curl\n", f"curl=8.5.0-2ubuntu10.1 # pinned: snapshot={self.JANUARY}\n")
        self.edit("images/toolsets/core/packages.txt", "curl\n",
                  f"curl=8.5.0-2ubuntu10.1 # pinned: {self.ISSUE} snapshot={self.JANUARY}\n")
        entries = self.entries(self.resolve())
        self.assertEqual([line for line in entries if line.startswith("pinned apt/curl")],
                         [f"pinned apt/curl {self.ISSUE}"])

    def test_an_apt_pin_naming_two_issues_fails(self):
        self.pin_curl_from_january()
        self.edit("images/cloud/base/packages.txt", "curl\n",
                  f"curl=8.5.0-2ubuntu10.1 # pinned: https://x/1 snapshot={self.JANUARY}\n")
        self.edit("images/toolsets/core/packages.txt", "curl\n",
                  f"curl=8.5.0-2ubuntu10.1 # pinned: https://x/2 snapshot={self.JANUARY}\n")
        result = self.run_lock("resolve", "linux")
        self.assertEqual(result.returncode, 1)
        self.assertIn("apt/curl's pin names two issues", result.stderr)

    # Ubuntu replaced openssl 3.0.13-0ubuntu3.6 in noble-updates: today's index
    # (and a newer snapshot's) lists only 3.0.13-0ubuntu3.16, the January
    # snapshot still 3.0.13-0ubuntu3.6 (snapshot.ubuntu.com, checked 2026-11-15).
    OPENSSL_TODAY = "openssl (3.0.13-0ubuntu3.16 Ubuntu:24.04/noble-updates [amd64])\n"
    OPENSSL_PINNED = "openssl (3.0.13-0ubuntu3.6 Ubuntu:24.04/noble-updates [amd64])"
    JANUARY = "20260101T000000Z"

    def pin_openssl(self, comment):
        self.apt.write_text(APT_TODAY + self.OPENSSL_TODAY)
        self.apt_snapshots = {self.JANUARY: [self.OPENSSL_PINNED]}
        self.edit("images/toolsets/core/packages.txt", "git\n", f"git\nopenssl=3.0.13-0ubuntu3.6{comment}\n")

    def test_a_pin_to_a_replaced_version_resolves_from_its_snapshot(self):
        self.pin_openssl(f"  # pinned: https://github.com/lolay/agent-images/issues/1 snapshot={self.JANUARY}")
        entries = self.entries(self.resolve())
        self.assertIn("apt openssl 3.0.13-0ubuntu3.6", entries)
        self.assertIn(f"apt-pin-snapshot openssl 3.0.13-0ubuntu3.6@{self.JANUARY}", entries)
        self.assertIn("pinned apt/openssl https://github.com/lolay/agent-images/issues/1", entries)
        self.assertIn("apt-snapshot - 20261106T220000Z", entries, "the type's own snapshot still moves")
        log = self.log.read_text().splitlines()
        self.assertIn(f"env APT_PINS=openssl=3.0.13-0ubuntu3.6@{self.JANUARY}", log)
        helper = self.repo / "images/cloud/base/apt-pins.sh"
        self.assertIn(f"mount type=bind,source={helper},target=/tmp/apt-pins.sh,readonly", log)

    def test_two_pins_from_one_snapshot_are_one_list(self):
        self.pin_openssl(f" # pinned: - snapshot={self.JANUARY}")
        self.apt_snapshots[self.JANUARY].append("libssl3t64 (3.0.13-0ubuntu3.6 Ubuntu:24.04/noble-updates [amd64])")
        self.edit("images/toolsets/core/packages.txt", "git\n",
                  f"git\nlibssl3t64=3.0.13-0ubuntu3.6 # pinned: - snapshot={self.JANUARY}\n")
        entries = self.entries(self.resolve())
        self.assertIn("apt libssl3t64 3.0.13-0ubuntu3.6", entries)
        self.assertIn(f"apt-pin-snapshot libssl3t64 3.0.13-0ubuntu3.6@{self.JANUARY}", entries)
        self.assertIn(f"env APT_PINS=libssl3t64=3.0.13-0ubuntu3.6@{self.JANUARY} "
                      f"openssl=3.0.13-0ubuntu3.6@{self.JANUARY}", self.log.read_text().splitlines())

    def test_a_pin_the_base_already_has_still_reaches_the_build(self):
        # apt installs nothing for a version the base image has, so the lock has
        # no apt line for it; the pin line alone carries it to bake-args.
        self.pin_openssl(f" # pinned: - snapshot={self.JANUARY}")
        self.apt_installed = {"libssl3t64": "3.0.13-0ubuntu3.6"}
        self.edit("images/toolsets/core/packages.txt", "git\n",
                  f"git\nlibssl3t64=3.0.13-0ubuntu3.6 # pinned: - snapshot={self.JANUARY}\n")
        lock = self.resolve()
        entries = self.entries(lock)
        self.assertFalse([line for line in entries if line.startswith("apt libssl3t64 ")])
        self.assertIn(f"apt-pin-snapshot libssl3t64 3.0.13-0ubuntu3.6@{self.JANUARY}", entries)
        self.write_lock("linux", lock)
        result = self.run_lock("bake-args", "linux")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"*.args.APT_PINS=libssl3t64=3.0.13-0ubuntu3.6@{self.JANUARY} "
                      f"openssl=3.0.13-0ubuntu3.6@{self.JANUARY}", result.stdout.splitlines())

    def test_the_same_pin_in_two_lists_counts_once(self):
        # curl is in the base's list and core's; apt-pins.sh refuses a package
        # pinned twice, so identical pins reach it once.
        pin = f"curl=8.5.0-2ubuntu10.1 # pinned: - snapshot={self.JANUARY}\n"
        self.apt_snapshots = {self.JANUARY: ["curl (8.5.0-2ubuntu10.1 Ubuntu:24.04/noble-updates [amd64])"]}
        self.edit("images/cloud/base/packages.txt", "curl\n", pin)
        self.edit("images/toolsets/core/packages.txt", "curl\n", pin)
        entries = self.entries(self.resolve())
        self.assertEqual([line for line in entries if line.startswith("apt-pin-snapshot ")],
                         [f"apt-pin-snapshot curl 8.5.0-2ubuntu10.1@{self.JANUARY}"])
        self.assertIn(f"env APT_PINS=curl=8.5.0-2ubuntu10.1@{self.JANUARY}", self.log.read_text().splitlines())

    def test_a_pin_snapshot_newer_than_the_locked_one_moves_it(self):
        # The build refuses a pin snapshot newer than the type's: its whole index
        # is a source. So the old snapshot stays only if it's at least as new.
        self.pin_openssl(f" # pinned: - snapshot={self.JANUARY}")
        first = self.resolve()
        for locked, expected in (("20251201T000000Z", "20261107T000000Z"),
                                 (self.JANUARY, self.JANUARY),
                                 ("20260201T000000Z", "20260201T000000Z")):
            with self.subTest(locked):
                self.write_lock("linux", first.replace("apt-snapshot - 20261106T220000Z", f"apt-snapshot - {locked}"))
                again = self.resolve(LOCK_NOW="20261107T000000Z")
                self.assertIn(f"apt-snapshot - {expected}", self.entries(again))

    def test_a_pin_to_a_replaced_version_without_its_snapshot_fails(self):
        # The gap the snapshot closes: today's archive no longer has the version.
        self.pin_openssl("  # pinned: https://github.com/lolay/agent-images/issues/1")
        result = self.run_lock("resolve", "linux")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("E: Version '3.0.13-0ubuntu3.6' for 'openssl' was not found", result.stderr)

    def test_a_pin_from_a_snapshot_that_lacks_it_fails(self):
        self.pin_openssl(" # pinned: - snapshot=20261101T000000Z")
        self.apt_snapshots["20261101T000000Z"] = [self.OPENSSL_TODAY.strip()]
        result = self.run_lock("resolve", "linux")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("for 'openssl' was not found", result.stderr)

    def test_a_malformed_snapshot_pin_fails_before_apt(self):
        cases = {
            "no version": "git\nopenssl # pinned: - snapshot=20260101T000000Z\n",
            "not an id": "git\nopenssl=3.0.13-0ubuntu3.6 # pinned: - snapshot=january\n",
            "glued to the URL": "git\nopenssl=3.0.13-0ubuntu3.6 # pinned: https://x/1,snapshot=20260101T000000Z\n",
        }
        for name, line in cases.items():
            with self.subTest(name):
                path = self.repo / "images/toolsets/core/packages.txt"
                saved = path.read_text()
                self.log.unlink(missing_ok=True)
                self.edit("images/toolsets/core/packages.txt", "git\n", line)
                result = self.run_lock("resolve", "linux")
                path.write_text(saved)
                self.assertNotEqual(result.returncode, 0, name)
                self.assertEqual(result.stdout, "", name)
                self.assertIn("openssl", result.stderr, name)
                self.assertFalse(self.log.exists() and self.log_lines("apt "), f"{name}: apt ran")

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


BLENDER = "https://download.blender.org/release"
BLENDER_MIRROR = "https://mirrors.ocf.berkeley.edu/blender/release"
BLENDER_SH = REPO / "images/toolsets/3d/blender.sh"


class BlenderTests(unittest.TestCase):
    """images/toolsets/3d/blender.sh, which lock.sh and the 3d toolset both read
    Blender through, against a fake curl that refuses the way blender.org did
    (lock-watch run 43715897836: a 403 that curl's --retry leaves alone)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "bin").mkdir()
        executable(self.tmp / "bin/curl", FAKE_CURL)
        self.log = self.tmp / "log"
        self.urls = {}
        for source in (BLENDER, BLENDER_MIRROR):
            self.urls.update({
                f"{source}/": '<a href="Blender5.3/">Blender5.3/</a>\n<a href="Blender5.10/">Blender5.10/</a>\n',
                f"{source}/Blender5.10/": '<a href="blender-5.10.0-linux-x64.tar.xz">x</a>\n'
                                          '<a href="blender-5.10.1-linux-x64.tar.xz">x</a>\n'
                                          '<a href="blender-5.10.1-windows-x64.zip">x</a>\n',
                f"{source}/Blender5.10/blender-5.10.1.sha256": "abc  blender-5.10.1-linux-x64.tar.xz\n",
            })

    def env(self):
        (self.tmp / "urls.json").write_text(json.dumps(self.urls))
        return {**os.environ, "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}",
                "FAKE_URLS": str(self.tmp / "urls.json"), "FAKE_LOG": str(self.log)}

    def blender(self, *args):
        return subprocess.run(["bash", str(BLENDER_SH), *args], cwd=REPO, env=self.env(),
                              capture_output=True, text=True, timeout=60)

    def refuse(self, url, times):
        self.urls[f"status {url}"] = [403] * times

    def attempts(self, url):
        return self.log.read_text().splitlines().count(f"attempt {url}")

    def test_latest_is_the_newest_build_in_the_newest_series(self):
        # The series sort as versions (5.10 after 5.3), not as text.
        result = self.blender("latest")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "5.10.1\n")
        self.assertIn(f"blender: the release index from {BLENDER}\n", result.stderr)
        self.assertNotIn(BLENDER_MIRROR, self.log.read_text())

    def test_a_403_then_success_is_served_by_blender_org(self):
        self.refuse(f"{BLENDER}/", 1)
        self.refuse(f"{BLENDER}/Blender5.10/blender-5.10.1.sha256", 2)
        result = self.blender("latest")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "5.10.1\n")
        self.assertEqual(self.attempts(f"{BLENDER}/"), 2, "the 403, then the retry that served it")
        out = self.tmp / "blender.sha256"
        result = self.blender("get", "Blender5.10/blender-5.10.1.sha256", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"blender: Blender5.10/blender-5.10.1.sha256 from {BLENDER}\n", result.stderr)
        self.assertEqual(out.read_text(), "abc  blender-5.10.1-linux-x64.tar.xz\n")
        self.assertNotIn(BLENDER_MIRROR, self.log.read_text(), "the retry served it; no mirror was asked")

    def test_a_refusing_blender_org_falls_back_to_a_mirror_and_says_so(self):
        self.refuse(f"{BLENDER}/", 99)
        self.refuse(f"{BLENDER}/Blender5.10/", 99)
        result = self.blender("latest")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "5.10.1\n")
        self.assertEqual(self.attempts(f"{BLENDER}/"), 4, "one try and three retries before the mirror")
        self.assertIn(f"blender: {BLENDER}/ failed\n", result.stderr)
        self.assertIn(f"blender: the release index from {BLENDER_MIRROR}\n", result.stderr)
        self.assertIn(f"blender: Blender5.10/ from {BLENDER_MIRROR}\n", result.stderr)

    def test_a_transfer_that_breaks_off_leaves_nothing_behind(self):
        # blender.org sends part of an index naming a series the mirror doesn't
        # have, then drops the connection on every attempt. Only the mirror's
        # page may be read, or latest picks the series blender.org half-sent.
        self.urls[f"partial {BLENDER}/"] = '<a href="Blender9.9/">Blender9.9/</a>\n<a href="Blend'
        self.urls[f"status {BLENDER}/"] = ["partial"] * 99
        result = self.blender("latest")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "5.10.1\n")
        self.assertIn(f"blender: the release index from {BLENDER_MIRROR}\n", result.stderr)
        out = self.tmp / "blender.sha256"
        self.urls[f"partial {BLENDER}/Blender5.10/blender-5.10.1.sha256"] = "000  blender-5.10.1-linux"
        self.urls[f"status {BLENDER}/Blender5.10/blender-5.10.1.sha256"] = ["partial"] * 99
        result = self.blender("get", "Blender5.10/blender-5.10.1.sha256", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out.read_text(), "abc  blender-5.10.1-linux-x64.tar.xz\n")

    def test_every_source_refusing_fails(self):
        self.urls = {}
        result = self.blender("get", "Blender5.10/blender-5.10.1.sha256", str(self.tmp / "out"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("blender: no source served Blender5.10/blender-5.10.1.sha256", result.stderr)
        self.assertIn(f"blender: {BLENDER}/Blender5.10/blender-5.10.1.sha256 failed", result.stderr)
        result = self.blender("latest")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_an_index_with_no_linux_build_fails(self):
        self.urls[f"{BLENDER}/Blender5.10/"] = '<a href="blender-5.10.1-windows-x64.zip">x</a>\n'
        result = self.blender("latest")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no Linux build in Blender5.10/", result.stderr)

    def test_usage(self):
        for args in ([], ["latest", "x"], ["get"], ["nope"]):
            with self.subTest(args):
                self.assertEqual(self.blender(*args).returncode, 1)

    def lock_pins(self):
        locks = self.tmp / "locks/3d-gpu"
        locks.mkdir(parents=True)
        (locks / "versions.lock").write_text("release blender 5.3.0\npinned blender -\n")
        return subprocess.run(["bash", str(SCRIPT), "pins", "3d-gpu"], cwd=REPO,
                              env={**self.env(), "LOCK_DIR": str(self.tmp / "locks")},
                              capture_output=True, text=True, timeout=60)

    def test_lock_reads_blender_through_the_helper(self):
        # lock.sh's resolve and pins share release_latest, which asks the source
        # the 3d toolset declares (BLENDER_RELEASES); pins needs no Docker.
        self.refuse(f"{BLENDER}/", 1)
        result = self.lock_pins()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "blender\t5.3.0\t5.10.1\t-\n")
        self.assertEqual(self.attempts(f"{BLENDER}/"), 2)

    def test_lock_fails_when_blender_cant_be_read(self):
        self.urls = {}
        result = self.lock_pins()
        self.assertEqual(result.returncode, 1)
        self.assertIn("lock: couldn't read the newest release from images/toolsets/3d/blender.sh", result.stderr)


class ReleaseToolTests(unittest.TestCase):
    """lock.sh's release tools come from the toolset scripts that read them: a
    `NAME_RELEASES=<source>` line beside `${NAME_VERSION:-...}` (r4.f12). Run in
    a tree of one type and one toolset, so a tool lock.sh has never heard of
    shows that adding one is that declaration and nothing in lock.sh."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        (self.repo / "images/cloud/demo").mkdir(parents=True)
        (self.repo / "images/cloud/base").mkdir()
        (self.repo / "images/cloud/base/Dockerfile").write_text("FROM mirror.gcr.io/library/ubuntu:24.04\n")
        (self.repo / "images/cloud/demo/Dockerfile").write_text(
            "FROM agent-base\nRUN bash /tmp/toolsets/install-toolset.sh widgets\n")
        self.toolset = self.repo / "images/toolsets/widgets"
        self.toolset.mkdir(parents=True)
        (self.toolset / "latest.sh").write_text('#!/bin/bash\n[[ "$1" == latest ]] && echo 7.1.0\n')
        (self.tmp / "bin").mkdir()
        executable(self.tmp / "bin/curl", FAKE_CURL)
        (self.tmp / "urls.json").write_text(json.dumps({
            "redirect https://github.com/acme/gizmo/releases/latest": "https://github.com/acme/gizmo/releases/tag/v2.0.0",
        }))
        self.locks = self.tmp / "locks"
        (self.locks / "demo").mkdir(parents=True)

    def declare(self, text):
        (self.toolset / "install.sh").write_text("#!/bin/bash\n" + text)

    def pins(self, lock):
        (self.locks / "demo/versions.lock").write_text(lock)
        return subprocess.run(
            ["bash", str(SCRIPT), "pins", "demo"], cwd=self.repo, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "LOCK_DIR": str(self.locks),
                 "FAKE_URLS": str(self.tmp / "urls.json"), "FAKE_LOG": str(self.tmp / "log")})

    def test_a_new_tool_is_one_declaration_in_its_toolset(self):
        self.declare('gizmo="${GIZMO_VERSION:-latest}"\n'
                     "readonly GIZMO_RELEASES=https://github.com/acme/gizmo/releases\n"
                     'thing="${WIDGET_THING_VERSION:-latest}"\n'
                     "WIDGET_THING_RELEASES=latest.sh\n")
        result = self.pins("release gizmo v1.0.0\npinned gizmo -\nrelease widget-thing 7.0.0\npinned widget-thing -\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "gizmo\tv1.0.0\tv2.0.0\t-\nwidget-thing\t7.0.0\t7.1.0\t-\n")

    def test_a_pinned_tool_no_toolset_declares_fails(self):
        self.declare("set -euo pipefail\n")
        result = self.pins("release gizmo v1.0.0\npinned gizmo -\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("lock: pinned 'gizmo': no upstream known", result.stderr)

    def test_a_constraint_without_its_source_fails(self):
        # A deleted or unreadable declaration (export, declare -r) would drop the
        # tool from the lock, and the build would take its newest release.
        for line in ("", "export GIZMO_RELEASES=https://github.com/acme/gizmo/releases\n"):
            with self.subTest(line):
                self.declare('gizmo="${GIZMO_VERSION:-latest}"\n' + line)
                result = self.pins("release gizmo v1.0.0\npinned gizmo -\n")
                self.assertEqual(result.returncode, 1)
                self.assertIn("lock: images/toolsets/widgets/install.sh: ${GIZMO_VERSION:-...} has no "
                              "GIZMO_RELEASES=<source> beside it", result.stderr)

    def test_terraform_is_resolved_on_its_own(self):
        self.declare('terraform="${TERRAFORM_VERSION:-latest}"\n')
        result = self.pins("terraform - 1.14.2\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_an_unknown_type_fails(self):
        result = subprocess.run(["bash", str(SCRIPT), "releases", "nope"], cwd=self.repo,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("lock: no type nope (images/cloud/nope/Dockerfile)", result.stderr)


    def test_a_source_without_its_constraint_fails(self):
        self.declare("readonly GIZMO_RELEASES=https://github.com/acme/gizmo/releases\n")
        result = self.pins("release gizmo v1.0.0\npinned gizmo -\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("lock: no ${GIZMO_VERSION:-...} in images/toolsets/widgets/install.sh", result.stderr)

    def test_an_unknown_source_fails(self):
        for source, error in (("https://gitlab.com/acme/gizmo/-/releases", "unknown release source"),
                              ("missing.sh", "release source 'missing.sh' isn't a script beside it")):
            with self.subTest(source):
                self.declare(f'gizmo="${{GIZMO_VERSION:-latest}}"\nreadonly GIZMO_RELEASES={source}\n')
                result = self.pins("release gizmo v1.0.0\npinned gizmo -\n")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"lock: images/toolsets/widgets/install.sh: {error}", result.stderr)


class LockedReleaseToolTests(unittest.TestCase):
    """Every type's lock, against the repo's toolsets: each `release <name>` line
    is still a release tool its toolsets declare. Without this a pull request
    that deletes a declaration stays green, and the lock watch drops the tool
    from the lock after it merges (r4.f12's review)."""

    def test_every_locked_release_tool_is_declared(self):
        locks = sorted((REPO / "images/cloud").glob("*/versions.lock"))
        self.assertTrue(locks)
        for lock in locks:
            type_ = lock.parent.name
            with self.subTest(type_):
                result = subprocess.run(["bash", str(SCRIPT), "releases", type_], cwd=REPO,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                declared = {line.split()[0] for line in result.stdout.splitlines()}
                locked = {line.split()[1] for line in lock.read_text().splitlines() if line.startswith("release ")}
                self.assertLessEqual(locked, declared, f"{type_}: locked but not declared")


class PinBakeArgsTests(unittest.TestCase):
    """bake-args takes APT_PINS from the type's package lists, as install_apt takes
    a pin's version: a pull request that adds a snapshot pin builds with it while
    its lock is still main's (no Docker needed)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        for name in ("images/cloud", "images/toolsets", "infra/gcp", "scripts/cloud", "hooks/gcp"):
            shutil.copytree(REPO / name, self.repo / name)
        self.locks = self.tmp / "locks"
        (self.locks / "linux").mkdir(parents=True)
        # main's lock: no pin lines at all.
        (self.locks / "linux/versions.lock").write_text(ReadTests.LOCK)

    def add(self, list_name, old, new):
        path = self.repo / list_name
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def bake_args(self):
        return subprocess.run(["bash", str(self.repo / "scripts/cloud/lock.sh"), "bake-args", "linux"],
                              cwd=self.repo, capture_output=True, text=True, timeout=30,
                              env={**os.environ, "LOCK_DIR": str(self.locks)})

    def test_a_pin_added_before_the_lock_has_it_reaches_the_build(self):
        self.add("images/toolsets/core/packages.txt", "git\n",
                 "git\nopenssl=3.0.13-0ubuntu3.6 # pinned: https://x/1 snapshot=20260101T000000Z\n")
        self.add("images/toolsets/native/packages.txt", "cmake\n",
                 "cmake=3.28.3-1build7  # pinned: - snapshot=20250601T120000Z\n")
        result = self.bake_args()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("*.args.APT_PINS=cmake=3.28.3-1build7@20250601T120000Z "
                      "openssl=3.0.13-0ubuntu3.6@20260101T000000Z", result.stdout.splitlines())
        self.assertEqual(result.stdout.count("APT_PINS"), 1)

    def test_the_same_pin_in_two_lists_is_passed_once(self):
        pin = "curl=8.5.0-2ubuntu10.1 # pinned: - snapshot=20260101T000000Z\n"
        self.add("images/cloud/base/packages.txt", "curl\n", pin)
        self.add("images/toolsets/core/packages.txt", "curl\n", pin)
        result = self.bake_args()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("*.args.APT_PINS=curl=8.5.0-2ubuntu10.1@20260101T000000Z", result.stdout.splitlines())

    def test_a_pin_without_a_snapshot_adds_nothing(self):
        self.add("images/toolsets/native/packages.txt", "cmake\n", "cmake=3.28.3-1build7\n")
        result = self.bake_args()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("APT_PINS", result.stdout)

    def test_a_bad_pin_fails_before_printing_anything(self):
        # bake.sh accepts any non-empty output (r3.f3), so nothing may come first.
        self.add("images/toolsets/core/packages.txt", "git\n", "git\nopenssl # pinned: - snapshot=20260101T000000Z\n")
        result = self.bake_args()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("a snapshot needs a name=version pin", result.stderr)

    def test_an_apt_pin_naming_two_issues_fails_before_printing_anything(self):
        # The build never reads the issue, but the pull request that names two
        # fails in cloud-build's bake-args, not in the next resolve on main.
        self.add("images/cloud/base/packages.txt", "curl\n", "curl=8.5.0-2ubuntu10.1 # pinned: https://x/1\n")
        self.add("images/toolsets/core/packages.txt", "curl\n", "curl=8.5.0-2ubuntu10.1 # pinned: https://x/2\n")
        result = self.bake_args()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("apt/curl's pin names two issues: https://x/1 and https://x/2", result.stderr)

    def test_an_apt_pin_naming_the_same_issue_in_two_lists_is_fine(self):
        pin = "curl=8.5.0-2ubuntu10.1 # pinned: https://x/1\n"
        self.add("images/cloud/base/packages.txt", "curl\n", pin)
        self.add("images/toolsets/core/packages.txt", "curl\n", pin)
        self.assertEqual(self.bake_args().returncode, 0)


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

    def test_bake_args_ignores_the_locks_pin_lines(self):
        # A record only: APT_PINS comes from the package lists (PinBakeArgsTests),
        # and the repo's lists name no snapshot.
        self.write("linux/versions.lock", self.LOCK
                   + "apt-pin-snapshot openssl 3.0.13-0ubuntu3.6@20260101T000000Z\n"
                   + "pinned apt/cmake -\n")
        result = self.run_lock("bake-args", "linux")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("APT_PINS", result.stdout)

    def test_a_pin_snapshot_change_counts_as_its_package(self):
        old = self.write("old", self.LOCK + "apt-pin-snapshot sqlite3 3.45.1-1ubuntu2.8@20260101T000000Z\n")
        new = self.write("new", self.LOCK.replace("1ubuntu2.8", "1ubuntu2.5")
                         + "apt-pin-snapshot sqlite3 3.45.1-1ubuntu2.5@20251001T000000Z\n")
        self.assertIn("apt-pin-snapshot/sqlite3: 3.45.1-1ubuntu2.8@20260101T000000Z -> 3.45.1-1ubuntu2.5@20251001T000000Z",
                      self.run_lock("diff", old, new).stdout.splitlines())
        self.assertEqual(self.run_lock("summary", old, new).stdout.strip(), "apt 1 package")

    def test_a_pin_snapshot_change_alone_counts_its_package(self):
        # Only the pin line moves for a version the base image already has.
        old = self.write("old", self.LOCK + "apt-pin-snapshot zlib1g 1:1.3.dfsg-3.1ubuntu2@20260101T000000Z\n")
        new = self.write("new", self.LOCK + "apt-pin-snapshot zlib1g 1:1.3.dfsg-3.1ubuntu2@20260201T000000Z\n")
        self.assertEqual(self.run_lock("summary", old, new).stdout.strip(), "apt 1 package")

    def test_pins_reads_a_snapshot_pins_version_from_its_own_line(self):
        # libssl3t64's version is the base image's own, so apt installed nothing
        # and the lock has no apt line for it; openssl has one.
        self.write("linux/versions.lock", self.LOCK
                   + "apt openssl 3.0.13-0ubuntu3.6\n"
                   + "apt-pin-snapshot libssl3t64 3.0.13-0ubuntu3.6@20260101T000000Z\n"
                   + "apt-pin-snapshot openssl 3.0.13-0ubuntu3.6@20260101T000000Z\n"
                   + "pinned apt/libssl3t64 -\npinned apt/openssl -\n")
        result = self.run_lock("pins", "linux")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "apt/libssl3t64\t3.0.13-0ubuntu3.6\t-\t-",
            "apt/openssl\t3.0.13-0ubuntu3.6\t-\t-",
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

    def test_path_names_the_lock_whether_or_not_it_exists(self):
        self.write("linux/versions.lock", self.LOCK)
        for type_ in ("linux", "newtype"):
            with self.subTest(type_):
                result = self.run_lock("path", type_)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"{self.tmp}/{type_}/versions.lock\n")
        for args in (("path",), ("path", "../linux"), ("path", "linux", "x")):
            with self.subTest(args):
                result = self.run_lock(*args)
                self.assertEqual(result.returncode, 1)
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

    def test_a_failed_fetch_names_its_url(self):
        # lock-watch run 43715897836 failed on a bare "curl: (22) ... 403".
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        executable(bin_ / "curl", FAKE_CURL)
        (self.tmp / "urls.json").write_text(json.dumps({
            "status https://downloads.claude.ai/claude-code-releases/latest": [403]}))
        self.write("linux/versions.lock", self.LOCK + "pinned claude -\n")
        result = subprocess.run(
            ["bash", str(SCRIPT), "pins", "linux"], cwd=REPO, capture_output=True, text=True, timeout=30,
            env={**os.environ, "LOCK_DIR": str(self.tmp), "PATH": f"{bin_}:{os.environ['PATH']}",
                 "FAKE_URLS": str(self.tmp / "urls.json"), "FAKE_LOG": str(self.tmp / "log")})
        self.assertEqual(result.returncode, 1)
        self.assertIn("curl: (22) The requested URL returned error: 403", result.stderr)
        self.assertIn("lock: couldn't fetch https://downloads.claude.ai/claude-code-releases/latest", result.stderr)

    def test_usage(self):
        for args in ([], ["resolve"], ["bake-args"], ["diff", "a"], ["nope", "x"]):
            with self.subTest(args):
                self.assertEqual(self.run_lock(*args).returncode, 1)


if __name__ == "__main__":
    unittest.main()
