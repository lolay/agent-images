# Cloud session measurement report

Measurement only: nothing was installed, fixed or changed except the one `brew install hello` / `brew uninstall hello` pair in section 5. All times are UTC. Each section gives the command and its output.

## 1. Cache

Command:

```bash
cat /var/log/agent-images-setup.tsv
echo "CCR_SPAWN_TIMESTAMP_MS=$CCR_SPAWN_TIMESTAMP_MS"
date -u -d @$((CCR_SPAWN_TIMESTAMP_MS/1000))
date -u
```

Output:

```
setup	start	1793939640.936989304
core	ok	72.1
native	ok	4.3
jvm	ok	3.0
browser	ok	2.9
display	ok	4.5
node	ok	47.0
python	ok	13.1
go	ok	13.3
rust	ok	40.4
proto	ok	13.7
terraform	ok	12.8
setup	end	1793939868.172860475
--- CCR_SPAWN_TIMESTAMP_MS=1793940445812
spawn UTC: 2026-11-06T04:47:25Z
--- now
2026-11-06T04:47:35Z
```

Converted:

- Setup start: 2026-11-06 04:34:00.937Z
- Setup end: 2026-11-06 04:37:48.173Z (227.2 s; the toolset seconds sum to 227.1)
- Spawn (`CCR_SPAWN_TIMESTAMP_MS`): 2026-11-06 04:47:25.812Z
- First command of this session: about 04:47:35Z

The setup log ends 577 s before the spawn. This session started from the cache. The setup script did not run for this session.

## 2. What the script left in place

Command: `ls -l` and `stat` on each path, then `cat` on each file.

```
/etc/sudoers.d/agent-images
-r--r----- 1 root root 30 Nov  6 04:34 /etc/sudoers.d/agent-images
ubuntu ALL=(ALL) NOPASSWD:ALL

/opt/agent-images/bin/brew
-rwxr-xr-x 1 root root 74 Nov  6 04:37 /opt/agent-images/bin/brew
#!/bin/sh
exec sudo -u ubuntu -H /home/linuxbrew/.linuxbrew/bin/brew "$@"

/opt/agent-images/libexec/session-env
-rwxr-xr-x 1 root root 315 Nov  6 04:37 /opt/agent-images/libexec/session-env
#!/bin/sh
# SessionStart hook (scripts/anthropic/setup.sh): every Bash tool command
# sources the agent-images profile fragment.
cat >/dev/null
[ -n "${CLAUDE_ENV_FILE:-}" ] || exit 0
grep -qF zz-agent-images.sh "$CLAUDE_ENV_FILE" 2>/dev/null ||
	printf '. /etc/profile.d/zz-agent-images.sh\n' >>"$CLAUDE_ENV_FILE"

/root/.claude/settings.json
-rw-r--r-- 1 root root 212 Nov  6 04:37 /root/.claude/settings.json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/opt/agent-images/libexec/session-env"
          }
        ]
      }
    ]
  }
}

/etc/profile.d/zz-agent-images.sh
-rw-r--r-- 1 root root 1157 Nov  6 04:37 /etc/profile.d/zz-agent-images.sh
# agent-images toolsets (scripts/anthropic/setup.sh): Homebrew's tools ahead of
# the image's own runtimes, as in agent-linux. Sourced by login shells, root's
# ~/.bashrc and, through a SessionStart hook, every Bash tool command.
export HOMEBREW_PREFIX=/home/linuxbrew/.linuxbrew HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1
export RUSTUP_HOME=/home/ubuntu/.rustup TENV_ROOT=/home/ubuntu/.tenv TENV_AUTO_INSTALL=true
case ":$PATH:" in
*":/opt/agent-images/bin:"*) ;;
*) export PATH="/opt/agent-images/bin:$HOMEBREW_PREFIX/opt/rustup/bin:$HOMEBREW_PREFIX/bin:$HOMEBREW_PREFIX/sbin:$PATH" ;;
esac

# The display toolset: agent-linux's entrypoint starts Xvfb at :99 before the
# session; here there's no entrypoint, so the first shell starts it (detached,
# so it outlives that shell) and every shell points DISPLAY at it.
if [ -z "${DISPLAY:-}" ] && command -v Xvfb >/dev/null 2>&1; then
	export DISPLAY=:99
	if [ ! -S /tmp/.X11-unix/X99 ]; then
		(setsid Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &)
		for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
			[ -S /tmp/.X11-unix/X99 ] && break
			sleep 0.1
		done
	fi
fi

/home/linuxbrew
drwxr-xr-x 3 root root 4096 Nov  6 04:34 /home/linuxbrew
root:root 755
1.7G	/home/linuxbrew
/home/linuxbrew/.linuxbrew (prefix): ubuntu:ubuntu 755
```

All five files and the Homebrew tree exist. `/home/linuxbrew` is root-owned; the prefix under it is ubuntu-owned.

## 3. PATH and environment in the Bash tool's shell

Command: `echo "$PATH" | tr ':' '\n'`, plus `echo` of `RUSTUP_HOME`, `TENV_ROOT` and `DISPLAY`.

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

RUSTUP_HOME=/home/ubuntu/.rustup
TENV_ROOT=/home/ubuntu/.tenv
DISPLAY=:99
```

Ordering check (`grep -n` on the PATH lines):

```
1:/opt/agent-images/bin
3:/home/linuxbrew/.linuxbrew/bin
7:/root/.cargo/bin
8:/usr/local/go/bin
9:/opt/node22/bin
15:/usr/local/bin
```

`/opt/agent-images/bin` (1) and `/home/linuxbrew/.linuxbrew/bin` (3) come before `/usr/local/bin` (15), `/opt/node22/bin` (9), `/usr/local/go/bin` (8) and `/root/.cargo/bin` (7). Note that `/root/.cargo/bin` is at 7 and `/home/linuxbrew/.linuxbrew/opt/rustup/bin` at 2, so Homebrew's rustup wins for cargo and rustc.

Not measured: `CLAUDE_ENV_FILE` is unset in this shell. The profile is nonetheless applied: the running `ps` line for the Bash tool's wrapper includes `. /etc/profile.d/zz-agent-images.sh`.

## 4. Whose copy wins

Command: `command -v <tool>`, then the version command (`go version`, `tmux -V` where `--version` is not valid).

| Tool | `command -v` | Owner | Version |
| --- | --- | --- | --- |
| node | /home/linuxbrew/.linuxbrew/bin/node | Homebrew | v26.13.0 |
| python3 | /home/linuxbrew/.linuxbrew/bin/python3 | Homebrew | Python 3.15.2 |
| go | /home/linuxbrew/.linuxbrew/bin/go | Homebrew | go1.27.3 linux/amd64 |
| cargo | /home/linuxbrew/.linuxbrew/opt/rustup/bin/cargo | Homebrew | cargo 1.99.1 (5f94df478 2026-08-27) |
| rustc | /home/linuxbrew/.linuxbrew/opt/rustup/bin/rustc | Homebrew | rustc 1.99.1 (58cf8f9c3 2026-10-13) |
| gh | /home/linuxbrew/.linuxbrew/bin/gh | Homebrew | 2.103.3 |
| rg | /home/linuxbrew/.linuxbrew/bin/rg | Homebrew | ripgrep 15.2.0 |
| tmux | /home/linuxbrew/.linuxbrew/bin/tmux | Homebrew | tmux 3.8 |
| age | /home/linuxbrew/.linuxbrew/bin/age | Homebrew | v1.3.2 |
| uv | /home/linuxbrew/.linuxbrew/bin/uv | Homebrew | 0.13.7 |
| ruff | /home/linuxbrew/.linuxbrew/bin/ruff | Homebrew | 0.17.4 |
| sops | /home/linuxbrew/.linuxbrew/bin/sops | Homebrew | 3.13.3 |
| shellcheck | /home/linuxbrew/.linuxbrew/bin/shellcheck | Homebrew | 0.11.0 |
| actionlint | /home/linuxbrew/.linuxbrew/bin/actionlint | Homebrew | 1.7.12 |
| nowline | /home/linuxbrew/.linuxbrew/bin/nowline | Homebrew | 0.12.1 |
| triage | /home/linuxbrew/.linuxbrew/bin/triage | Homebrew | 0.5.0 |
| protoc | /home/linuxbrew/.linuxbrew/bin/protoc | Homebrew | libprotoc 36.2 |
| buf | /home/linuxbrew/.linuxbrew/bin/buf | Homebrew | 1.75.0 |
| terraform | /home/linuxbrew/.linuxbrew/bin/terraform | Homebrew | Terraform v1.17.1 |
| tflint | /usr/local/bin/tflint | Anthropic | TFLint version 0.65.0 |

Version column: the first line of each tool's version output, except shellcheck (version on line 2) and the commands noted above (`go version`, `tmux -V`). `triage` resolves to `/home/linuxbrew/.linuxbrew/Cellar/triage/0.5.0/bin/triage`. `tflint` is the only tool here from Anthropic's image; it is in `/usr/local/bin` from `images/toolsets/terraform/install.sh`.

## 5. Homebrew and display

Commands and outputs:

```
== brew install hello
==> Downloading Homebrew API data
✔︎ JSON API packages.x86_64_linux.jws.json
==> Downloading bottle manifests
✔︎ Bottle Manifest hello (2.12.3)
==> Fetching downloads for: hello
Warning: Landlock ABI 10 or later is required to deny all network access; found ABI 7. Applying the network restrictions supported by this kernel.
✔︎ Bottle hello (2.12.3)
==> Pouring hello--2.12.3.x86_64_linux.bottle.1.tar.gz
🍺  /home/linuxbrew/.linuxbrew/Cellar/hello/2.12.3: 58 files, 755.1KB
elapsed: 7.9 s

== hello
Hello, world!

== brew uninstall hello
Uninstalling /home/linuxbrew/.linuxbrew/Cellar/hello/2.12.3... (58 files, 755.1KB)
```

- `brew install hello`: 7.9 s wall clock, including the download of Homebrew's API data. Exit status was not captured because the output went through `tail`.
- `hello` printed `Hello, world!`.
- After uninstall, `brew list --formula` does not show hello, and `/home/linuxbrew/.linuxbrew/bin/hello` does not exist. A `command -v hello` run in the same shell right after the uninstall still printed the old path, which is bash's command hash table; a fresh lookup finds nothing.

Display:

```
$ xdpyinfo | head -5
name of display:    :99
version number:    11.0
vendor string:    The X.Org Foundation
vendor release number:    12101011
X.Org version: 21.1.11
```

`xdpyinfo` exited 141 (SIGPIPE from `head -5`); that is not a failure of the display. Xvfb is running on :99: `1083 root Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp`, socket `/tmp/.X11-unix/X99` present. `/tmp/xvfb.log` holds only xkbcomp keysym warnings, which its own message says are not fatal.

## 6. Smoke checks

Command: each line of `/etc/agent-images/toolsets/<name>.check` for every name in `toolsets.txt`, run with `bash -c` in the Bash tool's shell; PASS means exit 0.

| Toolset | Check | Result | First line of output |
| --- | --- | --- | --- |
| core | gh | PASS | gh version 2.103.3 (2026-10-26) |
| core | git | PASS | git version 2.43.0 |
| core | jq | PASS | jq-1.7 |
| core | rg | PASS | ripgrep 15.2.0 |
| core | tmux | PASS | tmux 3.8 |
| core | age | PASS | v1.3.2 |
| core | sops | PASS | sops 3.13.3 |
| core | actionlint | PASS | 1.7.12 |
| core | shellcheck | PASS | ShellCheck - shell script analysis tool (version 0.11.0 on line 2) |
| core | mandoc | PASS | /usr/bin/mandoc (`command -v`, no version printed) |
| core | cc | PASS | cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0 |
| native | cmake | PASS | cmake version 3.28.3 |
| native | pkg-config | PASS | 1.8.1 |
| native | openssl headers | PASS | 3.0.13 |
| jvm | java | PASS | first line is the `JAVA_TOOL_OPTIONS` notice; version is on line 2: openjdk version "21.0.12.1" |
| display | display | PASS | dimensions: 1920x1080 pixels |
| display | opengl | PASS | OpenGL renderer string: llvmpipe (LLVM 20.1.2, 256 bits) |
| display | screenshot | PASS | 1920x1080 PNG |
| node | node | PASS | v26.13.0 |
| node | pnpm | PASS | 12.16.1 |
| python | python3 | PASS | Python 3.15.2 |
| python | uv | PASS | uv 0.13.7 (Homebrew 2026-11-03 x86_64-unknown-linux-gnu) |
| python | ruff | PASS | ruff 0.17.4 |
| python | pre-commit | PASS | pre-commit 4.7.0 |
| go | go | PASS | go version go1.27.3 linux/amd64 |
| rust | cargo | PASS | cargo 1.99.1 (5f94df478 2026-08-27) |
| rust | rustc | PASS | rustc 1.99.1 (58cf8f9c3 2026-10-13) |
| proto | protoc | PASS | libprotoc 36.2 |
| proto | buf | PASS | 1.75.0 |
| proto | grpcurl | PASS | grpcurl 1.9.4 |
| terraform | terraform | PASS | Terraform v1.17.1 |
| terraform | tflint | PASS | TFLint version 0.65.0 |
| browser | (no `browser.check` file exists) | NOT RUN | `browser` is listed in `toolsets.txt` but has no check file, so there is nothing to run |

32 checks in the 10 toolsets that have check files: 32 pass, 0 fail. The browser toolset could not be checked.

## 7. Summary: what doesn't match `specs/anthropic-cloud.md` ("Matching agent-linux on Anthropic's cloud")

1. **The `/usr/local/bin` shims are missing.** The spec says the script puts symlinks for triage, nowline, sops, shellcheck, actionlint, pre-commit, protoc, buf and grpcurl into `/usr/local/bin`, plus a Terraform wrapper that sets `TENV_ROOT`. Measured:

   ```
   absent: /usr/local/bin/triage
   absent: /usr/local/bin/nowline
   absent: /usr/local/bin/sops
   absent: /usr/local/bin/shellcheck
   absent: /usr/local/bin/actionlint
   absent: /usr/local/bin/pre-commit
   absent: /usr/local/bin/protoc
   absent: /usr/local/bin/buf
   absent: /usr/local/bin/grpcurl
   absent: /usr/local/bin/terraform
   -rwxr-xr-x 1 root root 55611554 Nov  6 04:37 /usr/local/bin/tflint
   ```

   The session-start doctor reported `FAILED (exit 2): make doctor in /workspace/agent-images` with `triage not found`, which is the failure the spec says these shims fix. Cause (inferred, not confirmed): the block that creates them is in `scripts/anthropic/setup.sh` lines 130-142, and it was added by commit `1f592bc` ("give hooks on Anthropic's cloud the tools its image lacks (#31)"), dated 2026-11-06T04:46:49Z. This image's setup ran 04:34-04:37Z, nine minutes earlier, so the cached run most likely used an older script. The script that ran is not on this machine, so this is a timing inference.

2. **Hooks won't find Terraform.** The spec says the Terraform wrapper supplies `TENV_ROOT` for hooks. The wrapper is absent, and a hook was not run here, so whether `terraform` resolves in a hook is not measured.

3. **The spec's claim that `CLAUDE_ENV_FILE` carries the profile.** Not observable here: `CLAUDE_ENV_FILE` is unset in this shell, yet the profile is applied. The `ps` output shows the Bash tool's wrapper sourcing `/etc/profile.d/zz-agent-images.sh` directly. The result matches the spec; the mechanism described is not visible.

4. **Browser toolset.** `toolsets.txt` lists `browser`, but there is no `browser.check`, so its smoke check can't run. The spec doesn't say whether one should exist.

5. **Timings differ from the spec's figures** (not a mismatch in kind): `brew install hello` took 7.9 s here, against 5.5 s in the spec's fresh-environment run and 9.3 s in its cached report. The setup script took 227.2 s here, against 211.4 s in the spec's fresh run.

Matches the spec: the sudoers drop-in, the `brew` wrapper, the profile fragment and the session-env hook are present; Homebrew is root-owned at `/home/linuxbrew` with the prefix ubuntu-owned and 1.7 GB; PATH puts Homebrew and `/opt/agent-images/bin` ahead of `/usr/local/bin`, `/opt/node22/bin`, `/usr/local/go/bin` and `/root/.cargo/bin`; every tool but tflint resolves to Homebrew; all 32 smoke checks pass; Xvfb runs at :99 with DISPLAY=:99.

Not measured: `make doctor` was not run (not requested). The `Environment doctor` output quoted above is the session-start hook's output, as received.
