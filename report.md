# agent-images measurement, Anthropic cloud session, 2026-11-06

Facts only. Each section gives the command and its output. Nothing was installed or fixed. The one change to the environment is step 5, which overwrote `/opt/agent-images/build-fingerprint` for one run and restored it byte-for-byte (verified with `cmp`).

Session branch: `claude/agent-images-measurement-xd1c48` (HEAD `ebc0588` at start).
`$CLAUDE_PID` is 90 (`/opt/node22/bin/claude --preload /home/claude/.claude/remote/spare.sock`, started 04:55:51 UTC). This shell is a descendant of PID 90.

## 1. Build

Command: `cat /var/log/agent-images-setup.tsv`, `echo $CCR_SPAWN_TIMESTAMP_MS`, `date -u -d @...`

```
setup	start	1793941077.374131725
core	ok	65.8
native	ok	5.0
jvm	ok	2.7
browser	ok	2.6
display	ok	5.0
node	ok	40.4
python	ok	18.3
go	ok	11.9
rust	ok	33.9
proto	ok	12.1
terraform	ok	10.8
setup	end	1793941286.044303022
```

| | UTC |
| --- | --- |
| setup start (`1793941077.374`) | 2026-11-06T04:57:57.374Z |
| setup end (`1793941286.044`) | 2026-11-06T05:01:26.044Z |
| `CCR_SPAWN_TIMESTAMP_MS=1793941286130` | 2026-11-06T05:01:26.130Z |

Setup wall time: 208.67 s. Sum of the stage times: 208.5 s. Setup end is 86 ms before the spawn timestamp.

The script ran for this session, not from the cache. Its log ends within a fraction of a second of this session's spawn, and the log was written at 05:01 for this container.

## 2. Session-start doctor

The personal plugin's SessionStart hook output, quoted verbatim:

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

Triage is not reported missing. The doctor ran `triage --profile linux` and printed its check tree. All nine errors are host or self-hosted-runner items (VT-x, nested virtualization, `.env`, LXD, `lxc`, `vms/runner.env`, the runner secret, lingering). None concerns a tool in the image.

No other SessionStart notice mentioned agent-images. The build-fingerprint notice did not appear at start, which matches step 4.

## 3. Hook PATH (#31)

Command: `ls -l /usr/local/bin/<tool>` for each tool.

```
lrwxrwxrwx 1 root root 37 Nov  6 05:01 /usr/local/bin/triage -> /home/linuxbrew/.linuxbrew/bin/triage
lrwxrwxrwx 1 root root 38 Nov  6 05:01 /usr/local/bin/nowline -> /home/linuxbrew/.linuxbrew/bin/nowline
lrwxrwxrwx 1 root root 35 Nov  6 05:01 /usr/local/bin/sops -> /home/linuxbrew/.linuxbrew/bin/sops
lrwxrwxrwx 1 root root 41 Nov  6 05:01 /usr/local/bin/shellcheck -> /home/linuxbrew/.linuxbrew/bin/shellcheck
lrwxrwxrwx 1 root root 41 Nov  6 05:01 /usr/local/bin/actionlint -> /home/linuxbrew/.linuxbrew/bin/actionlint
lrwxrwxrwx 1 root root 41 Nov  6 05:01 /usr/local/bin/pre-commit -> /home/linuxbrew/.linuxbrew/bin/pre-commit
lrwxrwxrwx 1 root root 37 Nov  6 05:01 /usr/local/bin/protoc -> /home/linuxbrew/.linuxbrew/bin/protoc
lrwxrwxrwx 1 root root 34 Nov  6 05:01 /usr/local/bin/buf -> /home/linuxbrew/.linuxbrew/bin/buf
lrwxrwxrwx 1 root root 38 Nov  6 05:01 /usr/local/bin/grpcurl -> /home/linuxbrew/.linuxbrew/bin/grpcurl
-rwxr-xr-x 1 root root 106 Nov  6 05:01 /usr/local/bin/terraform
```

Nine symlinks point into `/home/linuxbrew/.linuxbrew/bin`. `terraform` is a regular file, not a symlink.

Command: `cat /usr/local/bin/terraform`

```
#!/bin/sh
TENV_ROOT="${TENV_ROOT:-/home/ubuntu/.tenv}" exec /home/linuxbrew/.linuxbrew/bin/terraform "$@"
```

`/home/linuxbrew/.linuxbrew/bin/terraform` is itself a symlink to `../Cellar/tenv/4.15.1/bin/terraform`. It is a tenv shim, and it runs `tenv` from PATH. `/home/linuxbrew/.linuxbrew/bin/tenv` exists, owned by `ubuntu`.

Command: `PATH` from `tr '\0' '\n' < /proc/90/environ`, then `env -i HOME=/root PATH=<that> sh -c '...'`

Claude Code's PATH:

```
/nix/var/nix/profiles/default/bin:/root/.local/bin:/root/.cargo/bin:/usr/local/go/bin:/opt/node22/bin:/opt/maven/bin:/opt/gradle/bin:/opt/rbenv/bin:/root/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

Under that PATH, each tool's version command:

```
triage: triage 0.5.0 (commit 21ac3fc0e67d78708e60dc7018640c68f76fe449, built 2026-10-13T05:25:09Z)
nowline: 0.12.1
sops: sops 3.13.3 (latest)
shellcheck: version: 0.11.0
actionlint: 1.7.12
pre-commit: pre-commit 4.7.0
protoc: libprotoc 36.2
buf: 1.75.0
grpcurl: grpcurl 1.9.4
terraform: Failure during terraform call : exec: "tenv": executable file not found in $PATH   (exit 1)
```

Under the same PATH, `command -v` one name at a time, with the version command:

```
node: /opt/node22/bin/node            node --version   -> v22.23.4
go: /usr/local/go/bin/go              go version       -> go version go1.24.7 linux/amd64
python3: /usr/local/bin/python3       python3 --version -> Python 3.13.18   (/usr/local/bin/python3 -> python3.13)
cargo: /root/.cargo/bin/cargo         cargo --version  -> cargo 1.97.0 (c980f4866 2026-06-30)   (cargo -> rustup)
terraform: /usr/local/bin/terraform   terraform version -> exit 1, tenv not found (above)
```

Result under the hook PATH: triage, nowline, sops, shellcheck, actionlint, pre-commit, protoc, buf and grpcurl run. Terraform does not. The wrapper sets `TENV_ROOT`, but the brew terraform still needs `tenv` on PATH, and the hook PATH has no `/home/linuxbrew/.linuxbrew/bin`.

## 4. Build fingerprint (#32)

Command: `cat /opt/agent-images/build-fingerprint` and `cat /opt/agent-images/libexec/build-fingerprint`

```
ae2ad066d80d52c4796caad34483c148c05a018a87e48e203824031aaefbcb6e /workspace/agent-images
```

```
#!/bin/sh
# Prints the fingerprint of an agent-images checkout's setup inputs.
git -C "$1" ls-files -s scripts/anthropic/setup.sh images/toolsets 2>/dev/null |
	sha256sum | cut -d' ' -f1
```

Command: `/opt/agent-images/libexec/build-fingerprint /workspace/agent-images` (the path the record names)

```
ae2ad066d80d52c4796caad34483c148c05a018a87e48e203824031aaefbcb6e
```

The hash matches the recorded one.

Command: `echo '{}' | /opt/agent-images/libexec/session-env`

```
(no output; exit 0)
```

## 5. The notice

Commands, in order: copy the record to `/tmp`, replace the hash with 64 zeros, run `echo '{}' | session-env | jq .`, restore from `/tmp`, verify with `cmp`.

Original record (copied to `/tmp/build-fingerprint.orig`):

```
ae2ad066d80d52c4796caad34483c148c05a018a87e48e203824031aaefbcb6e /workspace/agent-images
```

Record during the test:

```
0000000000000000000000000000000000000000000000000000000000000000 /workspace/agent-images
```

`echo '{}' | /opt/agent-images/libexec/session-env | jq .` (hook exit 0):

```json
{
  "systemMessage": "agent-images: this environment was built from an older scripts/anthropic/setup.sh or images/toolsets than this checkout's, and Anthropic's cache won't rebuild on its own. To update it, edit the environment's setup script (a comment line is enough); the next session reruns it.",
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "agent-images: this environment was built from an older scripts/anthropic/setup.sh or images/toolsets than this checkout's, and Anthropic's cache won't rebuild on its own. To update it, edit the environment's setup script (a comment line is enough); the next session reruns it. Tell the user this before starting work."
  }
}
```

Restore: `cp /tmp/build-fingerprint.orig /opt/agent-images/build-fingerprint && cmp ...` printed `restored: identical`. Final content:

```
ae2ad066d80d52c4796caad34483c148c05a018a87e48e203824031aaefbcb6e /workspace/agent-images
```

## 6. Bash tool shell

Command: `echo "$PATH" | tr ':' '\n'`

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
node -> /home/linuxbrew/.linuxbrew/bin/node      v26.13.0
go -> /home/linuxbrew/.linuxbrew/bin/go          go1.27.3 linux/amd64
python3 -> /home/linuxbrew/.linuxbrew/bin/python3  Python 3.15.2
cargo -> /home/linuxbrew/.linuxbrew/opt/rustup/bin/cargo  cargo 1.99.1 (5f94df478 2026-08-27)
terraform -> /home/linuxbrew/.linuxbrew/bin/terraform  Terraform v1.17.1 (on linux_amd64)
```

Toolsets (`/etc/agent-images/toolsets/toolsets.txt`): core, native, jvm, browser, display, node, python, go, rust, proto, terraform.

Check lines run: every non-comment line of each `<name>.check`, as `label: command`, each command run in bash, exit 0 required.

| Toolset | Lines | Pass | Fail |
| --- | --- | --- | --- |
| core | 11 | 11 | 0 |
| native | 3 | 3 | 0 |
| jvm | 1 | 1 | 0 |
| browser | no `browser.check` | - | - |
| display | 3 | 3 | 0 |
| node | 2 | 2 | 0 |
| python | 4 | 4 | 0 |
| go | 1 | 1 | 0 |
| rust | 2 | 2 | 0 |
| proto | 3 | 3 | 0 |
| terraform | 2 | 2 | 0 |
| **Total** | **32** | **32** | **0** |

Command: `ls -l /etc/agent-images/toolsets/browser.check`

```
ls: cannot access '/etc/agent-images/toolsets/browser.check': No such file or directory
```

Pass count: 32 of 32 check lines pass. `browser` is listed in toolsets.txt, and its setup-log line reads `browser	ok	2.6`, but it has no check file, so it contributes zero checked lines.

Check output, each line:

```
core: gh version 2.103.3 (2026-10-26); git version 2.43.0; jq-1.7; ripgrep 15.2.0; tmux 3.8;
      age v1.3.2; sops 3.13.3; actionlint 1.7.12; ShellCheck (version 0.11.0); mandoc /usr/bin/mandoc;
      cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0
native: cmake version 3.28.3; pkg-config 1.8.1; openssl 3.0.13
jvm: java (first line is the JAVA_TOOL_OPTIONS notice)
display: dimensions 1920x1080; OpenGL renderer llvmpipe (LLVM 20.1.2, 256 bits); screenshot 1920x1080 PNG
node: v26.13.0; pnpm 12.16.1
python: Python 3.15.2; uv 0.13.7; ruff 0.17.4; pre-commit 4.7.0
go: go1.27.3
rust: cargo 1.99.1; rustc 1.99.1 (58cf8f9c3 2026-10-13)
proto: libprotoc 36.2; buf 1.75.0; grpcurl 1.9.4
terraform: Terraform v1.17.1; TFLint version 0.65.0
```

Two earlier harness runs were wrong (they ran comment lines and whole `label: command` strings). Their results are not counted.

## 7. Summary: against `specs/anthropic-cloud.md`

Matches:

- **Fresh run, not cache.** The setup log ends 86 ms before the spawn timestamp, as the spec says a fresh run does. The 208.7 s run is within the spec's five-minute budget (about 91 s spare), and it is faster than the spec's 211.4 s and 246.5 s runs.
- **Fingerprint.** The record matches, the session-env hook is silent on a match, and the mismatch notice fires with the text the spec gives.
- **Hook PATH carries the #31 links.** Triage, nowline, sops, shellcheck, actionlint, pre-commit, protoc, buf and grpcurl resolve under Claude Code's PATH.
- **Hook language runtimes are Anthropic's.** Node 22.23.4, Go 1.24.7, Python 3.13.18 and Rust 1.97 are Anthropic's versions, as the spec says.
- **Bash-shell versions match the agent-linux column.** Node 26.13, Go 1.27.3, Python 3.15.2, Rust 1.99.1 and Terraform 1.17.1 are the Homebrew copies. Every tool version in the spec's agent-linux column that I checked matches (gh 2.103, ripgrep 15.2, tmux 3.8, age 1.3, sops 3.13, shellcheck 0.11, actionlint 1.7.12, pre-commit 4.7, protoc 36.2, buf 1.75, grpcurl 1.9.4, Terraform 1.17.1, tflint 0.65, triage 0.5.0, nowline 0.12.1).
- **Smoke-check count.** The spec says all 32 smoke checks pass. All 32 checks in the present check files pass.

Mismatches:

1. **Terraform does not work under the hook PATH.** The spec's Matching section describes the `/usr/local/bin/terraform` wrapper as the fix that gives hooks Terraform, and it implies the hook sees a working tool. Measured: `terraform version` under Claude Code's PATH fails with `exec: "tenv": executable file not found in $PATH`. The wrapper sets `TENV_ROOT` but does not put `/home/linuxbrew/.linuxbrew/bin` (where `tenv` lives) on PATH. A repo's `make doctor` that calls terraform from a hook will fail.
2. **`browser` has no smoke check.** `toolsets.txt` lists `browser`, and the setup log shows it installed (2.6 s), but there is no `browser.check`, so nothing verifies it. The spec's "32 smoke checks" counts only the checks that exist.
3. **`make doctor` fails in every cloud session.** The spec says the personal plugin runs `make doctor` at session start, and the spec's "Lolay's customizations" section does not mention a failure. This session's doctor exits 2 with nine errors, all for host or self-hosted-runner items (VT-x, LXD, `.env`, `vms/runner.env`, the runner secret, lingering). The doctor runs and triage is present, but a cloud session will always report FAILED. The spec should say so, or the repo's doctor should skip those items for the Anthropic profile.

Not measured: the spec's cold-start cache behaviour (the snapshot's timing and the 7-day expiry), the `EXPECTED_SPAWN_SECONDS` value, the browser toolset's libraries and Chromium (no check), the Homebrew disk size, and the `brew` wrapper's ownership.
