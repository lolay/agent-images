# agent-images measurement, Anthropic cloud session

Session: https://claude.ai/code/session_01BNKwNZmNx4ahudYCJCi9FB
Checkout: /workspace/agent-images, HEAD `154787f` (#33), branch `claude/busy-maxwell-mypumq`
Date: 2026-11-06. All times UTC. Nothing was installed, fixed or changed, except the
temporary fingerprint edit in step 5, which was restored and verified.

## 1. Build

```
$ cat /var/log/agent-images-setup.tsv
setup	start	1793941703.190493690
core	ok	71.8
native	ok	6.0
jvm	ok	5.4
browser	ok	3.0
display	ok	4.5
node	ok	44.0
python	ok	13.1
go	ok	13.3
rust	ok	37.5
proto	ok	13.2
terraform	ok	12.6
setup	end	1793941927.474623227

$ echo "CCR_SPAWN_TIMESTAMP_MS=$CCR_SPAWN_TIMESTAMP_MS"
CCR_SPAWN_TIMESTAMP_MS=1793941927764

$ echo "setup start UTC: $(date -u -d @1793941703 +%Y-%m-%dT%H:%M:%S).190Z"
setup start UTC: 2026-11-06T05:08:23.190Z
$ echo "setup end UTC:   $(date -u -d @1793941927 +%Y-%m-%dT%H:%M:%S).474Z"
setup end UTC:   2026-11-06T05:12:07.474Z
$ echo "spawn UTC:       $(date -u -d @1793941927 +%Y-%m-%dT%H:%M:%S).764Z"
spawn UTC:       2026-11-06T05:12:07.764Z
```

| | UTC |
| --- | --- |
| setup start (log) | 2026-11-06 05:08:23.190 |
| setup end (log) | 2026-11-06 05:12:07.475 |
| CCR_SPAWN_TIMESTAMP_MS | 2026-11-06 05:12:07.764 |

- Setup ran 224.3 s. The per-toolset times sum to 224.4 s (rounding).
- The log ends 0.289 s before the spawn timestamp. The script ran for this session, not from the cache. A cached session's log is from an earlier run and predates the spawn by tens of minutes.

## 2. Session-start doctor

The SessionStart output headed "Environment doctor", verbatim, as the personal plugin delivered it:

```
Environment doctor, run at session start by the personal plugin:
FAILED (exit 2): `make doctor` in /workspace/agent-images
    triage --profile linux --var image_ref=agent-android
    triage (profile: linux)

    [✗] Host
        [✓] x86_64
        [✗] VT-x or AMD-V — VT-x or AMD-V: exit 1 (want 0): 0 — # turn on virtualization (VT-x or SVM mode) in the firmware settings
        [✗] nested virtualization (the emulator runs inside each VM) — nested virtualization (the emulator runs inside each VM): output does not contain "Y" — make host-setup
        [✓] make
        [✗] .env — .env not found — make init
    [✗] LXD
        [✗] lxc — lxc not found — make host-setup
        [✗] LXD storage pool on ZFS (copy-on-write clones) — LXD storage pool on ZFS (copy-on-write clones): output does not contain "zfs": sh: 1: lxc: not found — make host-setup
        [✗] image agent-android available — image agent-android available: exit 127 (want 0): sh: 1: lxc: not found — make build
    [✗] Runners
        [✓] claude
        [✗] vms/runner.env — vms/runner.env not found — cp vms/example-linux.env vms/runner.env
        [✗] Claude environment secret in ~/.config/agent-images — Claude environment secret in ~/.config/agent-images: exit 1 (want 0) — make secret-set NAME=claude-environment-secret
        [✗] lingering (the orchestrator starts at boot) — lingering (the orchestrator starts at boot): output does not contain "yes": System has not been booted with systemd as init system (PID 1). Can't operate. — make host-setup

    ✗ 9 errors, 0 warnings, 3 ok — fix the [✗] items above
    To fix, run:
        # turn on virtualization (VT-x or SVM mode) in the firmware settings
        make host-setup
        make init
        make build
        cp vms/example-linux.env vms/runner.env
        make secret-set NAME=claude-environment-secret
    make: *** [Makefile:148: doctor] Error 1
```

(Reproduced from the SessionStart message.)

- Triage is not reported missing. The doctor ran `triage --profile linux` and reported its checks.
- No other SessionStart notice in this session mentions agent-images. The `session-env` hook printed nothing on the real record (step 4).
- The doctor's failures are the Linux-host checks (VT-x, LXD, `.env`, runner files, lingering), which a cloud session does not have.

## 3. Hook PATH (#31)

```
$ ls -l /usr/local/bin/<tool>, then readlink -f
(owner, size and date columns elided: all root, Nov 6 05:12)
triage:     lrwxrwxrwx ... /usr/local/bin/triage -> /home/linuxbrew/.linuxbrew/bin/triage
            -> /home/linuxbrew/.linuxbrew/Cellar/triage/0.5.0/bin/triage
nowline:    lrwxrwxrwx ... /usr/local/bin/nowline -> /home/linuxbrew/.linuxbrew/bin/nowline
            -> /home/linuxbrew/.linuxbrew/Cellar/nowline/0.12.1/bin/nowline
sops:       lrwxrwxrwx ... /usr/local/bin/sops -> /home/linuxbrew/.linuxbrew/bin/sops
            -> /home/linuxbrew/.linuxbrew/Cellar/sops/3.13.3/bin/sops
shellcheck: lrwxrwxrwx ... /usr/local/bin/shellcheck -> /home/linuxbrew/.linuxbrew/bin/shellcheck
            -> /home/linuxbrew/.linuxbrew/Cellar/shellcheck/0.11.0/bin/shellcheck
actionlint: lrwxrwxrwx ... /usr/local/bin/actionlint -> /home/linuxbrew/.linuxbrew/bin/actionlint
            -> /home/linuxbrew/.linuxbrew/Cellar/actionlint/1.7.12/bin/actionlint
pre-commit: lrwxrwxrwx ... /usr/local/bin/pre-commit -> /home/linuxbrew/.linuxbrew/bin/pre-commit
            -> /home/linuxbrew/.linuxbrew/Cellar/pre-commit/4.7.0_2/libexec/bin/pre-commit
protoc:     lrwxrwxrwx ... /usr/local/bin/protoc -> /home/linuxbrew/.linuxbrew/bin/protoc
            -> /home/linuxbrew/.linuxbrew/Cellar/protobuf/36.2/bin/protoc-36.2.0
buf:        lrwxrwxrwx ... /usr/local/bin/buf -> /home/linuxbrew/.linuxbrew/bin/buf
            -> /home/linuxbrew/.linuxbrew/Cellar/buf/1.75.0/bin/buf
grpcurl:    lrwxrwxrwx ... /usr/local/bin/grpcurl -> /home/linuxbrew/.linuxbrew/bin/grpcurl
            -> /home/linuxbrew/.linuxbrew/Cellar/grpcurl/1.9.4/bin/grpcurl
terraform:  -rwxr-xr-x 150 bytes (a wrapper script, not a link)
```

```
$ cat /usr/local/bin/terraform
#!/bin/sh
PATH="/home/linuxbrew/.linuxbrew/bin:$PATH" TENV_ROOT="${TENV_ROOT:-/home/ubuntu/.tenv}" exec /home/linuxbrew/.linuxbrew/bin/terraform "$@"
```

Claude Code's PATH (`tr '\0' '\n' < /proc/90/environ`, CLAUDE_PID=90):

```
PATH=/nix/var/nix/profiles/default/bin:/root/.local/bin:/root/.cargo/bin:/usr/local/go/bin:/opt/node22/bin:/opt/maven/bin:/opt/gradle/bin:/opt/rbenv/bin:/root/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

Run with `env -i HOME=/root PATH=<that PATH> sh -c '...'`:

```
$ triage --version
triage 0.5.0 (commit 21ac3fc0e67d78708e60dc7018640c68f76fe449, built 2026-10-13T05:25:09Z)   [exit 0]
$ nowline --version
0.12.1   [exit 0]
$ sops --version
sops 3.13.3 (latest)
[warning] Note that in a future version, sops will no longer check whether the current version is the latest ...   [exit 0]
$ shellcheck --version
ShellCheck - shell script analysis tool / version: 0.11.0 / ...   [exit 0]
$ actionlint --version
1.7.12 / installed from Homebrew / built with go1.26.3 compiler for linux/amd64   [exit 0]
$ pre-commit --version
pre-commit 4.7.0   [exit 0]
$ protoc --version
libprotoc 36.2   [exit 0]
$ buf --version
1.75.0   [exit 0]
$ grpcurl --version
grpcurl 1.9.4   [exit 0]
$ terraform version
Terraform v1.17.1 / on linux_amd64   [exit 0]

$ command -v node go python3 cargo
node: /opt/node22/bin/node
go: /usr/local/go/bin/go
python3: /usr/local/bin/python3
cargo: /root/.cargo/bin/cargo
```

- All nine linked tools run under the hook's PATH, and terraform runs through the wrapper (v1.17.1).
- node, go, python3 and cargo resolve to Anthropic's image copies. Their versions under this PATH were not run; the step asked for `command -v` only.

## 4. Build fingerprint (#32)

```
$ cat /opt/agent-images/build-fingerprint
d713081c2eb10caf95dfe8c4bff77a9265fb8e1b1b06a6d1c44ffd662b4af9d1 /workspace/agent-images

$ cat /opt/agent-images/libexec/build-fingerprint
#!/bin/sh
# Prints the fingerprint of an agent-images checkout's setup inputs.
git -C "$1" ls-files -s scripts/anthropic/setup.sh images/toolsets 2>/dev/null |
	sha256sum | cut -d' ' -f1

$ /opt/agent-images/libexec/build-fingerprint /workspace/agent-images
d713081c2eb10caf95dfe8c4bff77a9265fb8e1b1b06a6d1c44ffd662b4af9d1
```

The recorded hash is `d713081c2eb10caf95dfe8c4bff77a9265fb8e1b1b06a6d1c44ffd662b4af9d1`. **It matches.**

```
$ echo '{}' | /opt/agent-images/libexec/session-env
(no output, exit 0)
```

## 5. The notice

Temporary edit, then restored. The original was saved to `/tmp/build-fingerprint.orig`
(the user asked for `/tmp`). Checksums were compared before and after.

```
original sha256: 5220e1a2376ada827714a1f0a012a72be845ad9ba3c6e39f04d6e666dce33405
original content: d713081c2eb10caf95dfe8c4bff77a9265fb8e1b1b06a6d1c44ffd662b4af9d1 /workspace/agent-images
modified content: 0000000000000000000000000000000000000000000000000000000000000000 /workspace/agent-images

$ echo '{}' | /opt/agent-images/libexec/session-env | jq .
{
  "systemMessage": "agent-images: this environment was built from an older scripts/anthropic/setup.sh or images/toolsets than this checkout's, and Anthropic's cache won't rebuild on its own. To update it, edit the environment's setup script (a comment line is enough); the next session reruns it.",
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "agent-images: this environment was built from an older scripts/anthropic/setup.sh or images/toolsets than this checkout's, and Anthropic's cache won't rebuild on its own. To update it, edit the environment's setup script (a comment line is enough); the next session reruns it. Tell the user this before starting work."
  }
}
session-env exit=0

restored sha256: 5220e1a2376ada827714a1f0a012a72be845ad9ba3c6e39f04d6e666dce33405
RESTORED: matches original (cmp ok)
restored content: d713081c2eb10caf95dfe8c4bff77a9265fb8e1b1b06a6d1c44ffd662b4af9d1 /workspace/agent-images
-rw-r--r-- 1 root root 89 Nov  6 05:12 /opt/agent-images/build-fingerprint
```

## 6. Bash tool shell

PATH, one directory per line:

```
/opt/agent-images/bin
/home/linuxbrew/.linuxbrew/opt/rustup/bin
/home/linuxbrew/.linuxbrew/bin
/home/linuxbrew/.linuxbrew/sbin
/nix/var/nix/profiles/default/bin
/root/.local/bin
/root/.cargo/bin
/usr/local/go/bin
/opt/node22/bin
/opt/maven/bin
/opt/gradle/bin
/opt/rbenv/bin
/root/.bun/bin
/usr/local/sbin
/usr/local/bin
/usr/sbin
/usr/bin
/sbin
/bin
```

`command -v` and version:

```
node: /home/linuxbrew/.linuxbrew/bin/node      -> v26.13.0
go: /home/linuxbrew/.linuxbrew/bin/go          -> go version go1.27.3 linux/amd64
python3: /home/linuxbrew/.linuxbrew/bin/python3 -> Python 3.15.2
cargo: /home/linuxbrew/.linuxbrew/opt/rustup/bin/cargo -> cargo 1.99.1 (5f94df478 2026-08-27)
terraform: /home/linuxbrew/.linuxbrew/bin/terraform -> Terraform v1.17.1
```

Environment (for the spec's claims):

```
DISPLAY=:99 RUSTUP_HOME=/home/ubuntu/.rustup TENV_ROOT=/home/ubuntu/.tenv
15044 Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp
$ command -v tflint  -> /usr/local/bin/tflint
$ command -v brew    -> /opt/agent-images/bin/brew
```

Toolset checks. Each `<name>.check` line was run as `bash -c "<command>"` with a 30 s timeout. Lines were split on the first `: `. Below, each passing check shows its first output line only; the full output of each run is in the session transcript, and no check failed.

```
== core       PASS gh, git, jq, rg, tmux, age, sops, actionlint, shellcheck, mandoc, cc   (11/11)
== native     PASS cmake, pkg-config, openssl headers                                     (3/3)
== jvm        PASS java  (first line is the JAVA_TOOL_OPTIONS notice, as the spec predicts) (1/1)
== browser    no browser.check in /etc/agent-images/toolsets (the repo's images/toolsets/browser has no check.txt either) -> 0 lines
== display    PASS display (dimensions 1920x1080), opengl (llvmpipe), screenshot (1920x1080 PNG)   (3/3)
== node       PASS node v26.13.0, pnpm 12.16.1                                             (2/2)
== python     PASS python3 3.15.2, uv 0.13.7, ruff 0.17.4, pre-commit 4.7.0                (4/4)
== go         PASS go go1.27.3                                                              (1/1)
== rust       PASS cargo 1.99.1, rustc 1.99.1                                               (2/2)
== proto      PASS protoc 36.2, buf 1.75.0, grpcurl 1.9.4                                  (3/3)
== terraform  PASS terraform v1.17.1, tflint 0.65.0                                         (2/2)

TOTAL: 32 lines run, 32 pass, 0 fail.
```

Failures: none.

## 7. Summary: against specs/anthropic-cloud.md

Checked against "Cold start" and "Matching agent-linux on Anthropic's cloud".

| Spec claim | Measured here | Verdict |
| --- | --- | --- |
| Log predates spawn means cache; this run ended just before spawn | Log ends 0.289 s before spawn | Match: ran for this session |
| Previous runs 246.5 s, 166 s, 211.4 s, 208.7 s | This run 224.3 s | New data point. The table has no row for it |
| "246.5 s leaves under a minute of the five-minute budget" | 224.3 s leaves about 76 s | Consistent; the margin figure is from the older run |
| Session-start doctor found `triage` | Doctor ran `triage`; not reported missing | Match |
| Doctor still exits 2 on a cloud session (Linux-host checks) | Exit 2; host, LXD, `.env`, runner checks | Match |
| Fingerprint matched, hook silent; zeroed record prints the notice | Match; silent; JSON notice printed | Match |
| Under the hook's PATH, nine linked tools run; Node, Go, Python, Rust are Anthropic's | All nine run; `command -v` shows `/opt/node22`, `/usr/local/go`, `/usr/local/bin/python3`, `/root/.cargo` | Match (versions not run under that PATH) |
| Terraform failed under the hook PATH (`tenv` not found); the wrapper's PATH entry fixes it | Terraform runs under the hook PATH (v1.17.1); the wrapper contains the Homebrew PATH entry (#33) | Match; the fix is in place |
| Bash shell PATH starts with `/opt/agent-images/bin` and Homebrew; `RUSTUP_HOME`, `TENV_ROOT`, `DISPLAY=:99` set | All true | Match |
| Every tool but tflint resolves to Homebrew's copy | node, go, python3, cargo, terraform, brew resolve to Homebrew; tflint is `/usr/local/bin/tflint` | Match |
| Xvfb runs at `:99` | `Xvfb :99` running (pid 15044) | Match |
| All 32 smoke checks pass | 32 of 32 | Match |
| Hook applies in the session that runs the script | The SessionStart fingerprint hook ran and printed nothing, as expected for a current record | Match |
| Hooks saw Anthropic's PATH and reported `triage` missing (#31's problem) | Doctor finds `triage` now | Fixed: the links are present |
| Profile fragment reaches Bash commands through `CLAUDE_ENV_FILE`, and "neither `/etc/profile.d` nor `~/.bashrc` reaches the commands" | `CLAUDE_ENV_FILE` is unset inside Bash commands. `/root/.bashrc` line 104 sources the fragment, and the fragment's own comment says `~/.bashrc` sources it. The fragment's effects are present | **Doesn't match, or can't be checked**: the spec's routing account doesn't match the `.bashrc` line; which route delivered the env is not determined here |
| `display` adds `xdpyinfo`, `glxinfo`, `xdotool`, Open Sans | `xdpyinfo`, `glxinfo` pass. `xdotool` and fonts not checked | Partly measured |

The one real discrepancy is the `~/.bashrc` routing claim: the spec says `~/.bashrc` doesn't reach Bash tool commands, but `setup.sh` writes a `.bashrc` line and the profile fragment's own header says `~/.bashrc` sources it. Which route is live here is not measured. The rest matches, and the cold-start table needs this session's 224.3 s row.

## Not measured

- Click-to-first-command wall time (only the setup and spawn timestamps are available here).
- Queue or spawn wait, and `EXPECTED_SPAWN_SECONDS`.
- Whether the next session reuses this snapshot, and how long the snapshot took to become usable.
- Node, Go, Python and Cargo versions under the hook PATH (only `command -v` was asked for).
- The `browser` toolset: no smoke check is defined for it.
- Whether `xdotool` and the Open Sans font are present.
- Which route delivers the profile fragment to Bash tool commands (see section 7).
