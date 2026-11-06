---
name: agent-toolset-checks
description: Check this runner image's toolchains with agent-toolset-checks, the runner the image's own smoke uses. Binary checks show each tool is installed and its version; smoke tests show the toolchain builds and runs something. On a failure, offer to file an issue on lolay/agent-images. Use when the user runs /agent-toolset-checks, asks whether the image's tools work or which versions it has, or a toolchain fails in a way that looks like the image rather than the project.
---

# agent-toolset-checks

This session runs on an agent-images runner image (lolay/agent-images). The image
records every toolset it installed, and `agent-toolset-checks` runs that record's
checks. There are two kinds:

- A **binary check** proves a tool is installed and runs, and prints its version.
- A **smoke test** is the least real work that shows a toolchain runs, such as Go
  building and running hello. Some are marked live: they need something only some
  hosts have, such as a GPU.

`/etc/agent-images/versions.md` is what the image contains. It holds the type,
build date, commit, Claude Code's version, every Homebrew formula and every apt
package. The design is lolay/agent-images `specs/gcp.md`, m4.

Run only the commands below. Never parse the record under
`/etc/agent-images/toolsets/` yourself, and never run a smoke test's script
directly: the runner gives each test its temp directory, time limit and offline
settings.

| Command | What it does |
| --- | --- |
| `agent-toolset-checks versions` | Every binary check, `label: version`; exits 1 if any failed |
| `agent-toolset-checks smoke-list` | The toolsets with a smoke test, in install order |
| `agent-toolset-checks live <name>` | Prints why, and exits 0, if `<name>`'s smoke test runs only live |
| `agent-toolset-checks smoke <name>` | Runs one smoke test; exit 124 means it timed out |

## 1. Ask what to run

Read the first lines of `/etc/agent-images/versions.md` (type, built, commit) and
`agent-toolset-checks smoke-list`. Then ask with the choice dialog:

- **Everything** (recommended): the binary checks and every smoke test.
- **Binary checks only**: fast; versions and presence.
- **Pick smoke tests**: then ask which, from `smoke-list` only. The dialog takes up
  to four options per question, so split a longer list across questions.

For each live test (`agent-toolset-checks live <name>` exits 0), say what it needs
before running it. If this session plainly lacks that (no `nvidia-smi` for "needs a
GPU"), say it can't run here and skip it rather than calling it a failure.

## 2. Run and report

Run what was chosen. Print one line per check, the way the image's own smoke does:
`pass smoke-go (go: built and ran hello)`, `FAIL smoke-rust (...)`, and for the
binary checks one line with how many passed and each failure in full. End with the
report's path.

## 3. On a failure, rerun once, then ask

Run each failed check once more, with full output (`agent-toolset-checks smoke
<name>`, or `agent-toolset-checks versions`), so you can tell a flake from a break.

Then ask with the choice dialog whether to file an issue on lolay/agent-images:
"File an issue" or "Don't file". When the rerun passed, say it looks flaky and
make "Don't file" the recommended option. Anything but a clear yes files nothing.

## 4. File it

Write the issue exactly as below, and show the body to the user before sending it.

**Title:** `<type>: <check> fails`, for example `linux: smoke-go fails` or
`gis: binary check gdal fails`.

**Body:**

````markdown
### What failed

<binary check `<label>` | smoke test `smoke-<name>`> (toolset `<name>`) on agent-<type>.
<Failed twice. | Failed once, then passed on a rerun.>

Rerun: `agent-toolset-checks <smoke <name> | versions>`
Exit status: <n> (124 is the 120 s limit)

### Image

- Type: <from versions.md>
- Built: <from versions.md>
- Commit: <from versions.md>
- Claude Code: <from versions.md>

### Output

```text
<the check's last 50 lines, from the rerun>
```

### Versions

```text
<only the lines from versions.md for this toolset's tools: its binary check
lines, and the Homebrew or apt lines of the packages they come from>
```

Filed from a session with `/agent-toolset-checks`.
````

Leave out anything from the user's own work: no project files or paths, no
environment dumps, no tokens, no repository names other than lolay/agent-images.
The test's temp directory is the only path that belongs in the output, and the
runner has already deleted it.

Before you create it, look for an open issue on lolay/agent-images with the same
title. If one exists, add the new output to it as a comment instead.

Create the issue with whatever GitHub access this session has (GitHub tools, or an
authenticated `gh`). If the session can't reach lolay/agent-images, give the user a
prefilled link instead:

```text
https://github.com/lolay/agent-images/issues/new?title=<url-encoded title>&body=<url-encoded body>
```

Then print the body as well, so they can paste it if the link is cut short. GitHub
limits a link to about 8,000 characters, so shorten the output section to keep it
under that.

Never file without the user's yes, and never file more than one issue per failure.
