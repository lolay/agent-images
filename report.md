# agent-images on Anthropic's cloud: session measurement

Measured 2026-11-06 in this session, branch `claude/adoring-goldberg-zbsr52`. Each
item shows the commands run and their output. Nothing was installed or fixed. The
only state change was `brew install hello` followed by `brew uninstall hello` (item 5).

## 1. Cache

```
$ cat /var/log/agent-images-setup.tsv
setup	start	1793939640.269921415
core	ok	66.9
native	ok	4.6
jvm	ok	2.5
browser	ok	2.4
display	ok	5.3
node	ok	41.4
python	ok	11.4
go	ok	18.3
rust	ok	34.4
proto	ok	12.3
terraform	ok	11.7
setup	end	1793939851.648617146

$ echo "CCR_SPAWN_TIMESTAMP_MS=$CCR_SPAWN_TIMESTAMP_MS"
CCR_SPAWN_TIMESTAMP_MS=1793939851684

$ date -u -d @1793939640 ; date -u -d @1793939851 ; date -u -d @1793939851.684
Fri Nov  6 04:34:00 UTC 2026
Fri Nov  6 04:37:31 UTC 2026
Fri Nov  6 04:37:31 UTC 2026
```

| Event | Epoch seconds | UTC |
| --- | --- | --- |
| Setup start | 1793939640.270 | 2026-11-06 04:34:00.27 |
| Setup end | 1793939851.649 | 2026-11-06 04:37:31.65 |
| Container spawn (`CCR_SPAWN_TIMESTAMP_MS`) | 1793939851.684 | 2026-11-06 04:37:31.68 |

- The script ran 211.4 s (end minus start). The per-toolset seconds sum to 211.2 s.
- The setup end is 0.036 s before the spawn stamp. Setup started 3 min 31 s before it.
- The files the script wrote are stamped inside that window (mtimes below), so this
  run's setup wrote them. A cached session would carry a log from an earlier build,
  with a start time well before its spawn.

```
$ stat -c '%n %U:%G %a %y' /etc/profile.d/zz-agent-images.sh /root/.claude/settings.json /opt/agent-images/libexec/session-env /var/log/agent-images-setup.tsv
/etc/profile.d/zz-agent-images.sh root:root 644 2026-11-06 04:37:31.635384314 +0000
/root/.claude/settings.json root:root 644 2026-11-06 04:37:31.642918342 +0000
/opt/agent-images/libexec/session-env root:root 755 2026-11-06 04:37:31.638298052 +0000
/var/log/agent-images-setup.tsv root:root 644 2026-11-06 04:37:31.647479809 +0000
```

Verdict: this session ran the script. The literal test in the measurement prompt
("log ends before the spawn") is met, but only by 36 ms. The mtimes and the 3.5-minute
run show the script ran for this container, not from a cache.

## 2. What the script left in place

```
$ ls -l /etc/sudoers.d/agent-images; cat /etc/sudoers.d/agent-images
-r--r----- 1 root root 30 Nov  6 04:34 /etc/sudoers.d/agent-images
ubuntu ALL=(ALL) NOPASSWD:ALL

$ ls -l /opt/agent-images/bin/brew; cat /opt/agent-images/bin/brew
-rwxr-xr-x 1 root root 74 Nov  6 04:37 /opt/agent-images/bin/brew
#!/bin/sh
exec sudo -u ubuntu -H /home/linuxbrew/.linuxbrew/bin/brew "$@"

$ ls -l /opt/agent-images/libexec/session-env; cat /opt/agent-images/libexec/session-env
-rwxr-xr-x 1 root root 315 Nov  6 04:37 /opt/agent-images/libexec/session-env
#!/bin/sh
# SessionStart hook (scripts/anthropic/setup.sh): every Bash tool command
# sources the agent-images profile fragment.
cat >/dev/null
[ -n "${CLAUDE_ENV_FILE:-}" ] || exit 0
grep -qF zz-agent-images.sh "$CLAUDE_ENV_FILE" 2>/dev/null ||
	printf '. /etc/profile.d/zz-agent-images.sh\n' >>"$CLAUDE_ENV_FILE"

$ ls -l /root/.claude/settings.json; cat /root/.claude/settings.json
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

$ ls -l /etc/profile.d/zz-agent-images.sh; cat /etc/profile.d/zz-agent-images.sh
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

$ ls -ld /home/linuxbrew; stat -c '%U:%G %a' /home/linuxbrew; du -sh /home/linuxbrew
drwxr-xr-x 3 root root 4096 Nov  6 04:34 /home/linuxbrew
root:root 755
1.7G	/home/linuxbrew

$ stat -c '%U:%G %n' /home/linuxbrew/.linuxbrew /home/linuxbrew/.linuxbrew/Cellar
ubuntu:ubuntu /home/linuxbrew/.linuxbrew
ubuntu:ubuntu /home/linuxbrew/.linuxbrew/Cellar
```

All five files exist. `/home/linuxbrew` itself is root:root 755 (1.7G in total).
Its `.linuxbrew` prefix and `Cellar` are owned by ubuntu:ubuntu.

## 3. PATH and environment in the Bash tool's shell

```
$ echo "$PATH" | tr ':' '\n'
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

$ echo "RUSTUP_HOME=$RUSTUP_HOME"; echo "TENV_ROOT=$TENV_ROOT"; echo "DISPLAY=$DISPLAY"
RUSTUP_HOME=/home/ubuntu/.rustup
TENV_ROOT=/home/ubuntu/.tenv
DISPLAY=:99
```

- `/opt/agent-images/bin` (position 1) and `/home/linuxbrew/.linuxbrew/bin` (position 3)
  both come before `/usr/local/bin` (15), `/opt/node22/bin` (9), `/usr/local/go/bin` (8)
  and `/root/.cargo/bin` (7).
- `RUSTUP_HOME` and `TENV_ROOT` are ubuntu's, as the profile sets them. `DISPLAY` is `:99`.
- I did not run `bash -lc 'echo $PATH'`, since it was not in the requested list.

## 4. Whose copy wins

`command -v` and the version each tool reports. Note: `go --version` and `tmux --version`
are not valid, so their versions come from `go version` and `tmux -V`.

| Tool | `command -v` | Version | Owner |
| --- | --- | --- | --- |
| node | /home/linuxbrew/.linuxbrew/bin/node | v26.13.0 | Homebrew |
| python3 | /home/linuxbrew/.linuxbrew/bin/python3 | Python 3.15.2 | Homebrew |
| go | /home/linuxbrew/.linuxbrew/bin/go | go version go1.27.3 linux/amd64 | Homebrew |
| cargo | /home/linuxbrew/.linuxbrew/opt/rustup/bin/cargo | cargo 1.99.1 (5f94df478 2026-08-27) | Homebrew |
| rustc | /home/linuxbrew/.linuxbrew/opt/rustup/bin/rustc | rustc 1.99.1 (58cf8f9c3 2026-10-13) | Homebrew |
| gh | /home/linuxbrew/.linuxbrew/bin/gh | gh version 2.103.3 (2026-10-26) | Homebrew |
| rg | /home/linuxbrew/.linuxbrew/bin/rg | ripgrep 15.2.0 | Homebrew |
| tmux | /home/linuxbrew/.linuxbrew/bin/tmux | tmux 3.8 | Homebrew |
| age | /home/linuxbrew/.linuxbrew/bin/age | v1.3.2 | Homebrew |
| uv | /home/linuxbrew/.linuxbrew/bin/uv | uv 0.13.7 | Homebrew |
| ruff | /home/linuxbrew/.linuxbrew/bin/ruff | ruff 0.17.4 | Homebrew |
| sops | /home/linuxbrew/.linuxbrew/bin/sops | sops 3.13.3 | Homebrew |
| shellcheck | /home/linuxbrew/.linuxbrew/bin/shellcheck | version: 0.11.0 | Homebrew |
| actionlint | /home/linuxbrew/.linuxbrew/bin/actionlint | 1.7.12 | Homebrew |
| nowline | /home/linuxbrew/.linuxbrew/bin/nowline | 0.12.1 | Homebrew |
| triage | /home/linuxbrew/.linuxbrew/bin/triage | triage 0.5.0 | Homebrew |
| protoc | /home/linuxbrew/.linuxbrew/bin/protoc | libprotoc 36.2 | Homebrew |
| buf | /home/linuxbrew/.linuxbrew/bin/buf | 1.75.0 | Homebrew |
| terraform | /home/linuxbrew/.linuxbrew/bin/terraform | Terraform v1.17.1 | Homebrew (tenv proxy) |
| tflint | /usr/local/bin/tflint | TFLint version 0.65.0 | Anthropic |

- `terraform` is a symlink to `../Cellar/tenv/4.15.1/bin/terraform`, so it is tenv
  from Homebrew, and it is ubuntu's (`/home/ubuntu/.tenv` exists).
- `tflint` has no Homebrew copy on disk (`ls: cannot access '/home/linuxbrew/.linuxbrew/bin/tflint'`).
- Anthropic's Node 22 (`/opt/node22/bin`), Go (`/usr/local/go/bin`) and root's cargo
  (`/root/.cargo/bin`) are on PATH but shadowed by Homebrew's copies.

## 5. Homebrew and display

```
$ command -v brew; time brew install hello
/opt/agent-images/bin/brew
==> Downloading Homebrew API data
✔︎ JSON API packages.x86_64_linux.jws.json
==> Downloading bottle manifests
✔︎ Bottle Manifest hello (2.12.3)
==> Fetching downloads for: hello
Warning: Landlock ABI 10 or later is required to deny all network access; found ABI 7. Applying the network restrictions supported by this kernel.
✔︎ Bottle hello (2.12.3)
==> Pouring hello--2.12.3.x86_64_linux.bottle.1.tar.gz
🍺  /home/linuxbrew/.linuxbrew/Cellar/hello/2.12.3: 58 files, 755.1KB
real	0m5.459s
user	0m3.282s
sys	0m1.685s

$ hello
Hello, world!

$ time brew uninstall hello
Uninstalling /home/linuxbrew/.linuxbrew/Cellar/hello/2.12.3... (58 files, 755.1KB)
real	0m0.899s

$ ls -l /home/linuxbrew/.linuxbrew/bin/hello
ls: cannot access '/home/linuxbrew/.linuxbrew/bin/hello': No such file or directory
```

- `brew install hello` took 5.5 s wall clock (the spec's earlier figure was 9.3 s).
- Right after the uninstall, `command -v hello` still printed the old path. That was
  bash's command hash, not a file: `ls` shows the file is gone.

```
$ xdpyinfo | head -5
name of display:    :99
version number:    11.0
vendor string:    The X.Org Foundation
vendor release number:    12101011
X.Org version: 21.1.11

$ ps -eo pid,user,args | grep '[X]vfb'
14942 root     Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp

$ ls -l /tmp/.X11-unix/
srwxrwxrwx 1 root root 0 Nov  6 04:37 X99
```

Xvfb is running on :99. It was started at 04:37:38 UTC by the first shell.

## 6. Smoke checks

Every line of each `/etc/agent-images/toolsets/<name>.check` was run in the Bash tool's
shell. Each command was required to exit 0. The first line of output is shown.

| Toolset | Check | Exit | First line of output | Result |
| --- | --- | --- | --- | --- |
| core | gh | 0 | gh version 2.103.3 (2026-10-26) | pass |
| core | git | 0 | git version 2.43.0 | pass |
| core | jq | 0 | jq-1.7 | pass |
| core | rg | 0 | ripgrep 15.2.0 | pass |
| core | tmux | 0 | tmux 3.8 | pass |
| core | age | 0 | v1.3.2 | pass |
| core | sops | 0 | sops 3.13.3 | pass |
| core | actionlint | 0 | 1.7.12 | pass |
| core | shellcheck | 0 | ShellCheck - shell script analysis tool (version line: 0.11.0) | pass |
| core | mandoc | 0 | /usr/bin/mandoc | pass |
| core | cc | 0 | cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0 | pass |
| native | cmake | 0 | cmake version 3.28.3 | pass |
| native | pkg-config | 0 | 1.8.1 | pass |
| native | openssl headers | 0 | 3.0.13 | pass |
| jvm | java | 0 | `Picked up JAVA_TOOL_OPTIONS: ...` (version line: `openjdk version "21.0.12.1" 2026-08-18`) | pass |
| browser | (no check file) | n/a | `/etc/agent-images/toolsets/browser.check` does not exist | not measured |
| display | display | 0 | dimensions: 1920x1080 pixels (488x274 millimeters) | pass |
| display | opengl | 0 | OpenGL renderer string: llvmpipe (LLVM 20.1.2, 256 bits) | pass |
| display | screenshot | 0 | 1920x1080 PNG | pass |
| node | node | 0 | v26.13.0 | pass |
| node | pnpm | 0 | 12.16.1 | pass |
| python | python3 | 0 | Python 3.15.2 | pass |
| python | uv | 0 | uv 0.13.7 (Homebrew 2026-11-03 x86_64-unknown-linux-gnu) | pass |
| python | ruff | 0 | ruff 0.17.4 | pass |
| python | pre-commit | 0 | pre-commit 4.7.0 | pass |
| go | go | 0 | go version go1.27.3 linux/amd64 | pass |
| rust | cargo | 0 | cargo 1.99.1 (5f94df478 2026-08-27) | pass |
| rust | rustc | 0 | rustc 1.99.1 (58cf8f9c3 2026-10-13) | pass |
| proto | protoc | 0 | libprotoc 36.2 | pass |
| proto | buf | 0 | 1.75.0 | pass |
| proto | grpcurl | 0 | grpcurl 1.9.4 | pass |
| terraform | terraform | 0 | Terraform v1.17.1 | pass |
| terraform | tflint | 0 | TFLint version 0.65.0 | pass |

32 checks in 10 toolsets pass, 0 fail. `browser` has no `.check` file, so nothing was run for it.
The installed `.check` files are byte-identical to `images/toolsets/<name>/check.txt`
in this checkout, except that `images/toolsets/browser/` has no `check.txt`.

## 7. Summary: what doesn't match `specs/anthropic-cloud.md`, "Matching agent-linux on Anthropic's cloud"

- **The SessionStart hook is described as untested.** The spec (line 249) says it is
  "untested in a session until the cache rebuilds". Here it works in the session that
  ran the script: the Bash tool's PATH starts with `/opt/agent-images/bin`, and
  `DISPLAY=:99`, both set by `/etc/profile.d/zz-agent-images.sh` through the hook.
- **The sudoers, wrapper and profile steps are not in the spec's measurement.** The
  spec says they "weren't run" (line 255). They are all in place here, as the
  fix describes.
- **The toolset timings are stale.** The spec's table (total 254.9 s, no script) and this
  run (211.2 s of toolset time, script run) differ per toolset:

  | Toolset | Spec (s) | This run (s) |
  | --- | --- | --- |
  | core | 79.5 | 66.9 |
  | native | 5.1 | 4.6 |
  | jvm | 3.0 | 2.5 |
  | browser | 3.2 | 2.4 |
  | display | not in table | 5.3 |
  | node | 51.8 | 41.4 |
  | python | 15.2 | 11.4 |
  | go | 14.8 | 18.3 |
  | rust | 49.8 | 34.4 |
  | proto | 16.6 | 12.3 |
  | terraform | 15.8 | 11.7 |

  Go is the only toolset that is slower. The two runs are not directly comparable: the
  spec's came from a different environment and cache state.
- **`brew install hello`** takes 5.5 s here, against the spec's 9.3 s.
- **Homebrew's ownership is split.** The spec says Homebrew "belongs to `ubuntu`". The
  `.linuxbrew` prefix and `Cellar` are ubuntu:ubuntu, but `/home/linuxbrew` itself is
  root:root 755.
- **Terraform matches.** The spec says tenv keeps Terraform per user. Here `terraform`
  is the Homebrew tenv proxy, so it works for root, and `TENV_ROOT` is `/home/ubuntu/.tenv`.
- **Java matches.** The `JAVA_TOOL_OPTIONS` notice is the first line of `java -version`.
- **Display matches.** `xdotool` is at `/usr/bin/xdotool`, and `fc-list` finds 13 Open
  Sans font files. I checked xdotool and the font only for existence, not for behavior.
- **The report prompt's check path is wrong for this image.** It says
  `images/toolsets` `check.txt`, which matches the checkout. The installed checks live
  at `/etc/agent-images/toolsets/<name>.check`, and the browser toolset has neither.

## Other observations (not in the requested list)

- The session-start hook reported `make doctor` as FAILED ("triage not found"). Now
  `triage` resolves to `/home/linuxbrew/.linuxbrew/bin/triage`. I can't tell from here
  whether the hook ran before the install finished.

```
$ make doctor
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
...
make: *** [Makefile:148: doctor] Error 1
exit=2
```

  Its host checks fail: there is no `/dev/kvm`, VT-x is not visible, and LXD is absent.
  These checks are for the linux VM profile, not for this cloud image.
- `sudo -n true` succeeds, and `apt-get` is 2.8.3.
