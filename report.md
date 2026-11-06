# agent-images measurement, session of 2026-11-06

Measured from inside this session. Nothing was installed, fixed or changed on the machine, except step 5, which installs and then uninstalls `hello` as the request asked.

One side effect to know about: the `terraform` login-shell check in step 6 ran tenv as root, which created `/root/.tenv/Terraform` (02:34 UTC). That directory is from my check run, not from the setup script.

Times below are UTC.

## 1. Cache

Command:

```
cat /var/log/agent-images-setup.tsv
echo $CCR_SPAWN_TIMESTAMP_MS
date -u -d @<epoch> '+%Y-%m-%d %H:%M:%S UTC'
```

Output (condensed):

```
setup	start	1793928300.439300868   -> 2026-11-06 01:25:00 UTC
setup	end	1793928546.917132242     -> 2026-11-06 01:29:06 UTC
CCR_SPAWN_TIMESTAMP_MS=1793932276035   -> 2026-11-06 02:31:16 UTC
```

The earlier read in this same session returned `CCR_SPAWN_TIMESTAMP_MS=1793931803899` (02:23:23 UTC). The value changed between reads, so it isn't a fixed per-session stamp.

The setup log ends 62 min before the spawn value. The setup ran before this session's spawn, so this session started from the environment cache. The two timestamps may come from different clocks, which I did not verify.

## 2. What the script left in place

Command and output:

```
ls -l /etc/sudoers.d/agent-images; cat /etc/sudoers.d/agent-images
-r--r----- 1 root root 30 Nov  6 01:25 /etc/sudoers.d/agent-images
ubuntu ALL=(ALL) NOPASSWD:ALL

ls -l /opt/agent-images/bin/brew; cat /opt/agent-images/bin/brew
-rwxr-xr-x 1 root root 74 Nov  6 01:29 /opt/agent-images/bin/brew
#!/bin/sh
exec sudo -u ubuntu -H /home/linuxbrew/.linuxbrew/bin/brew "$@"

ls -l /etc/profile.d/zz-agent-images.sh; cat /etc/profile.d/zz-agent-images.sh
-rw-r--r-- 1 root root 342 Nov  6 01:29 /etc/profile.d/zz-agent-images.sh
# agent-images toolsets (scripts/anthropic/setup.sh): Homebrew's tools ahead of
# the image's own runtimes, as in agent-linux.
export HOMEBREW_PREFIX=/home/linuxbrew/.linuxbrew HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1
export PATH="/opt/agent-images/bin:$HOMEBREW_PREFIX/opt/rustup/bin:$HOMEBREW_PREFIX/bin:$HOMEBREW_PREFIX/sbin:$PATH"

grep -n agent-images /root/.bashrc
104:. /etc/profile.d/zz-agent-images.sh

ls -ld /home/linuxbrew /home/linuxbrew/.linuxbrew; stat -c '%U:%G %a' /home/linuxbrew
drwxr-xr-x 3 root root 4096 Nov  6 01:25 /home/linuxbrew
drwxr-xr-x 14 ubuntu ubuntu 4096 Nov  6 01:25 /home/linuxbrew/.linuxbrew
root:root 755

du -sh /home/linuxbrew
1.7G	/home/linuxbrew
```

All four artifacts exist. `/home/linuxbrew/.linuxbrew` is owned by `ubuntu:ubuntu`. `/home/linuxbrew` itself is owned by root.

## 3. PATH

Commands: `echo "$PATH" | tr ':' '\n'`, `bash -lc 'echo "$PATH" | tr ":" "\n"'`, `bash -ic 'echo "$PATH" | tr ":" "\n"'`.

Tool shell (as-is):

```
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

`bash -lc`:

```
/opt/agent-images/bin
/home/linuxbrew/.linuxbrew/opt/rustup/bin
/home/linuxbrew/.linuxbrew/bin
/home/linuxbrew/.linuxbrew/sbin
/opt/ruby-3.3.6/bin
/opt/rbenv/bin
/opt/rbenv/shims
/opt/node22/bin
/opt/maven/bin
/usr/lib/jvm/java-21-openjdk-amd64/bin
/opt/gradle/bin
/root/.bun/bin
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

`bash -ic`:

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

Ahead of `/usr/local/bin`, `/opt/node22/bin`, `/usr/local/go/bin` and `/root/.cargo/bin`:

| Shell | /opt/agent-images/bin | /home/linuxbrew/.linuxbrew/bin |
|---|---|---|
| Tool shell | No, not on PATH | No, not on PATH |
| `bash -lc` | Yes (position 1) | Yes (position 3) |
| `bash -ic` | Yes (position 1) | Yes (position 3) |

The tool shell does not source `/etc/profile.d/zz-agent-images.sh`, so it never gets the Homebrew entries.

## 4. Whose copy wins (tool shell)

Commands: `command -v <tool>`, `type -a <tool>`, and the tool's version flag. The version flags were `--version`, `version` or `-V`, per tool.

| Tool | command -v | Version | type -a | Owner |
|---|---|---|---|---|
| node | /opt/node22/bin/node | v22.23.4 | /opt/node22/bin/node, /usr/local/bin/node | Anthropic's |
| python3 | /usr/local/bin/python3 | Python 3.13.18 | /usr/local/bin/python3, /usr/bin/python3, /bin/python3 | Anthropic's (Ubuntu's shadowed) |
| go | /usr/local/go/bin/go | go1.24.7 | /usr/local/go/bin/go | Anthropic's |
| cargo | /root/.cargo/bin/cargo | cargo 1.97.0 | /root/.cargo/bin/cargo | Anthropic's (/root) |
| rustc | /root/.cargo/bin/rustc | rustc 1.97.0 | /root/.cargo/bin/rustc | Anthropic's (/root) |
| gh | /usr/local/bin/gh | gh version 2.89.0 | /usr/local/bin/gh | Anthropic's |
| rg | /usr/bin/rg | ripgrep 14.1.0 | /usr/bin/rg, /bin/rg | Ubuntu's |
| tmux | /usr/bin/tmux | tmux 3.4 | /usr/bin/tmux, /bin/tmux | Ubuntu's |
| age | /usr/bin/age | 1.1.1 | /usr/bin/age, /bin/age | Ubuntu's |
| uv | /root/.local/bin/uv | uv 0.11.32 | /root/.local/bin/uv | Anthropic's (/root) |
| ruff | /root/.local/bin/ruff | ruff 0.16.9 | /root/.local/bin/ruff | Anthropic's (/root) |
| sops | (none) | (none) | not found | Missing from tool PATH. Homebrew copy exists: /home/linuxbrew/.linuxbrew/bin/sops, 3.13.3 |
| shellcheck | (none) | (none) | not found | Missing from tool PATH. Homebrew copy exists, version 0.11.0 (line 2 of `--version`) |
| actionlint | (none) | (none) | not found | Missing from tool PATH. Homebrew copy exists, 1.7.12 |
| nowline | (none) | (none) | not found | Missing from tool PATH. Homebrew copy exists, 0.12.1 |
| triage | (none) | (none) | not found | Missing from tool PATH. Homebrew copy exists, 0.5.0 |
| protoc | (none) | (none) | not found | Missing from tool PATH. Homebrew copy exists, libprotoc 36.2 |
| buf | (none) | (none) | not found | Missing from tool PATH. Homebrew copy exists, 1.75.0 |
| terraform | (none) | (none) | not found | Missing from tool PATH. Homebrew symlink exists but fails: `exec: "tenv": executable file not found in $PATH` |
| tflint | /usr/local/bin/tflint | TFLint version 0.65.0 | /usr/local/bin/tflint | Anthropic's |

The Homebrew versions for the "missing" rows were run by their full Homebrew path, because they aren't on the tool shell's PATH.

## 5. Homebrew from the session

Commands and output:

```
bash -lc 'command -v brew; brew --version | head -n1'
/opt/agent-images/bin/brew
Homebrew 7.0.12

command -v brew   (tool shell)
(not found)

time brew install hello      (via bash -lc)
==> Pouring hello--2.12.3.x86_64_linux.bottle.1.tar.gz
🍺  /home/linuxbrew/.linuxbrew/Cellar/hello/2.12.3: 58 files, 755.1KB
real	0m9.286s

hello
Hello, world!

stat -c '%U:%G %a %n' /home/linuxbrew/.linuxbrew/Cellar/hello/*
ubuntu:ubuntu 755 /home/linuxbrew/.linuxbrew/Cellar/hello/2.12.3
ls -l /home/linuxbrew/.linuxbrew/bin/hello
lrwxrwxrwx 1 ubuntu ubuntu 32 Nov  6 02:34 /home/linuxbrew/.linuxbrew/bin/hello -> ../Cellar/hello/2.12.3/bin/hello

brew uninstall hello
Uninstalling /home/linuxbrew/.linuxbrew/Cellar/hello/2.12.3... (58 files, 755.1KB)
real	0m1.036s

ls /home/linuxbrew/.linuxbrew/bin/hello
ls: cannot access '/home/linuxbrew/.linuxbrew/bin/hello': No such file or directory
```

The command exited with status 2 only because its final `ls` confirmed the uninstall. Brew printed a Landlock warning ("ABI 7 found, network restrictions partially applied") during install. It didn't affect the result.

Install took 9.3 s, including downloading the bottle. Uninstall took 1.0 s. Installed files were owned by `ubuntu:ubuntu`.

## 6. Smoke checks

Command: each line of `/etc/agent-images/toolsets/<name>.check` run in both shells with `eval`, exit code recorded. The `browser` toolset has no `.check` file, so it isn't measurable here.

Tool shell:

| Toolset | Check | Result | First output line |
|---|---|---|---|
| core | gh | PASS | gh version 2.89.0 |
| core | git | PASS | git version 2.43.0 |
| core | jq | PASS | jq-1.7 |
| core | rg | PASS | ripgrep 14.1.0 |
| core | tmux | PASS | tmux 3.4 |
| core | age | PASS | 1.1.1 |
| core | sops | FAIL (rc 127) | command not found |
| core | actionlint | FAIL (rc 127) | command not found |
| core | shellcheck | FAIL (rc 127) | command not found |
| core | mandoc | PASS | /usr/bin/mandoc |
| core | cc | PASS | cc 13.3.0 |
| native | cmake | PASS | cmake version 3.28.3 |
| native | pkg-config | PASS | 1.8.1 |
| native | openssl headers | PASS | 3.0.13 |
| jvm | java | PASS (version not captured) | first line is a JAVA_TOOL_OPTIONS notice |
| browser | (no .check file) | n/a | n/a |
| node | node | PASS | v22.23.4 |
| node | pnpm | PASS | 10.28.0 |
| python | python3 | PASS | Python 3.13.18 |
| python | uv | PASS | uv 0.11.32 |
| python | ruff | PASS | ruff 0.16.9 |
| python | pre-commit | FAIL (rc 127) | command not found |
| go | go | PASS | go1.24.7 |
| rust | cargo | PASS | cargo 1.97.0 |
| rust | rustc | PASS | rustc 1.97.0 |
| proto | protoc | FAIL (rc 127) | command not found |
| proto | buf | FAIL (rc 127) | command not found |
| proto | grpcurl | FAIL (rc 127) | command not found |
| terraform | terraform | FAIL (rc 127) | command not found |
| terraform | tflint | PASS | TFLint version 0.65.0 |

Login shell (`bash -lc`):

| Toolset | Check | Result | First output line |
|---|---|---|---|
| core | gh | PASS | gh version 2.103.3 |
| core | git | PASS | git version 2.43.0 |
| core | jq | PASS | jq-1.7 |
| core | rg | PASS | ripgrep 15.2.0 |
| core | tmux | PASS | tmux 3.8 |
| core | age | PASS | v1.3.2 |
| core | sops | PASS | sops 3.13.3 |
| core | actionlint | PASS | 1.7.12 |
| core | shellcheck | PASS | ShellCheck - shell script analysis tool (version 0.11.0 on line 2) |
| core | mandoc | PASS | /usr/bin/mandoc |
| core | cc | PASS | cc 13.3.0 |
| native | cmake | PASS | cmake version 3.28.3 |
| native | pkg-config | PASS | 1.8.1 |
| native | openssl headers | PASS | 3.0.13 |
| jvm | java | PASS (version not captured) | first line is a JAVA_TOOL_OPTIONS notice |
| browser | (no .check file) | n/a | n/a |
| node | node | PASS | v26.13.0 |
| node | pnpm | PASS | 12.16.1 |
| python | python3 | PASS | Python 3.15.2 |
| python | uv | PASS | uv 0.13.7 |
| python | ruff | PASS | ruff 0.17.4 |
| python | pre-commit | PASS | pre-commit 4.7.0 |
| go | go | PASS | go1.27.3 |
| rust | cargo | PASS | cargo 1.97.0 (resolves to Homebrew's rustup proxy) |
| rust | rustc | PASS | rustc 1.97.0 |
| proto | protoc | PASS | libprotoc 36.2 |
| proto | buf | PASS | 1.75.0 |
| proto | grpcurl | PASS | grpcurl 1.9.4 |
| terraform | terraform | FAIL (rc 42) | "No version files found for Terraform, fallback to latest-allowed strategy" |
| terraform | tflint | PASS | TFLint version 0.65.0 |

The `terraform` failure is the root user's tenv having no selected version. The setup script's Terraform 1.17.1 went into `/home/ubuntu/.tenv`, and `/home/ubuntu/.tenv/Terraform/version` reads `1.17.1`. Root's tenv has no version file.

## 7. Summary against specs/anthropic-cloud.md

Section references are to `specs/anthropic-cloud.md`: "Matching agent-linux on Anthropic's cloud" (its setup script description), and "Tool versions" (its agent-linux column).

| Item | Spec says | Measured | Match |
|---|---|---|---|
| Setup script runs | `scripts/anthropic/setup.sh` installs the toolsets, writes `/var/log/agent-images-setup.tsv` | Present, and the log exists | Yes |
| Homebrew owner | Owned by `ubuntu`, wrapper runs it as `ubuntu` | `/home/linuxbrew/.linuxbrew` is `ubuntu:ubuntu`; wrapper runs `sudo -u ubuntu` | Yes |
| sudoers, wrapper, profile | "weren't run" in the spec's own measurement | All three present | Differs from that measurement, as expected |
| Homebrew size | 1.7 GB | 1.7G | Yes |
| Cold start total | 254.9 s | 246.5 s (setup start to end) | No. Measured in a different session, so the numbers differ |
| Core | 79.5 s | 82.1 s | No (different run) |
| Node | 51.8 s | 52.3 s | No (different run) |
| Rust | 49.8 s | 42.7 s | No (different run) |
| gh | 2.103 | 2.103.3 (login shell) | Yes |
| ripgrep | 15.2 | 15.2.0 (login shell) | Yes |
| tmux | 3.8 | 3.8 (login shell) | Yes |
| age | 1.3 | v1.3.2 (login shell) | Yes |
| sops | 3.13 | 3.13.3 | Yes |
| shellcheck | 0.11 | 0.11.0 | Yes |
| actionlint | 1.7.12 | 1.7.12 | Yes |
| triage, nowline | 0.5.0, 0.12.1 | 0.5.0, 0.12.1 (Homebrew copies, run by full path) | Yes |
| Node | 26.13 | v26.13.0 (login shell) | Yes |
| pnpm | 12.16 | 12.16.1 (login shell) | Yes |
| Python | 3.15.2 | 3.15.2 (login shell) | Yes |
| uv, ruff | 0.13.7, 0.17.4 | 0.13.7, 0.17.4 (login shell) | Yes |
| pre-commit | 4.7 | 4.7.0 | Yes |
| Go | 1.27.3 | go1.27.3 (login shell) | Yes |
| Rust | 1.99.1 (rustup 1.29) | cargo and rustc 1.97.0 in both shells | **No** |
| protoc, buf, grpcurl | 36.2, 1.75, 1.9.4 | 36.2, 1.75.0, 1.9.4 (login shell) | Yes |
| Terraform, tflint | 1.17.1 through tenv 4.15; tflint 0.65 | tflint 0.65.0 passes; terraform fails as root (rc 42) | **No** for terraform as root |
| Browser | Chromium system libraries | No `.check` file for `browser`, not measured | Not measured |
| Anthropic side: Node, Go, Rust, uv, ruff, gh, rg, tmux, age, Python, pnpm (tool shell) | 22.23, 1.24.7, 1.97, 0.11.32, 0.16.9, 2.89, 14.1, 3.4, 1.1, 3.13.18, 10.28 | v22.23.4, go1.24.7, 1.97.0, 0.11.32, 0.16.9, 2.89.0, 14.1.0, 3.4, 1.1.1, 3.13.18, 10.28.0 | Yes |
| Homebrew "owned by the session user" | Spec's version table says Homebrew is owned by the session user | Owned by `ubuntu`; the session runs as root | Wording differs: the owner is not the session user |
| Proxy CA variables not passed to `ubuntu` | Spec's setup section | Not measured | Not measured |

### Mismatches

1. **Rust.** The spec's agent-linux column says 1.99.1 with rustup 1.29. Both shells here report 1.97.0. The login shell's `cargo` resolves to Homebrew's rustup proxy (`/home/linuxbrew/.linuxbrew/opt/rustup/bin/cargo`), so the toolchain itself is at 1.97.0.
2. **Terraform as root.** The login-shell check fails (rc 42): root's tenv has no version selected. The setup installed Terraform 1.17.1 under `/home/ubuntu/.tenv`, not `/root/.tenv`.
3. **Tool shell doesn't get the Homebrew tools.** Eight checks fail in the tool shell (`sops`, `actionlint`, `shellcheck`, `pre-commit`, `protoc`, `buf`, `grpcurl`, `terraform`). The profile script that puts the Homebrew tools first is sourced by login and interactive shells only, so anything run through the tool shell gets the Anthropic copies or nothing. In the tool shell, `terraform` and the others in step 4 are missing.
4. **Cold-start figures differ from the spec.** This run's setup took 246.5 s; the spec's measured run took 254.9 s. Per toolset the numbers also differ. This is a measurement difference, not a configuration mismatch.
5. **Java version not captured.** The `java -version` first line is the JAVA_TOOL_OPTIONS notice, so the check passes without a version line. Java's version is not in this report.
6. **Browser not measured.** `/etc/agent-images/toolsets/browser.check` doesn't exist, so nothing about the browser toolset could be checked here.
