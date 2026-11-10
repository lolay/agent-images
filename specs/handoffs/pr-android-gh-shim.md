put Claude Code's gh shim first on Android runners' PATH

Android runner VMs have the same signed-out `gh` that #67 fixed on Cloud Run.
They start Claude Code with the same `images/shared/runners/claude.sh`, and
`CLAUDE_USE_GIT_PROXY` defaults to true there (`gcp_runner_settings` never
overrides it), so sessions get the same per-session shim at
`$CLAUDE_CONFIG_DIR/ccr/bin/gh`. Two layers hid it: `profile.sh` puts Homebrew
first on every source (`bash -l`), and nothing re-adds the shim after the shell
snapshot's `export PATH=`, because Android had no SessionStart hook at all. This
fixes both and moves #67's two pieces into shared files, so the shim logic and
the env-file line each live once.

## Review guide

Review tier: [check], because every Linux runner session's PATH changes (Android
and Cloud Run) and the cloud image's settings merge changes.
Blast radius: Bash commands in Android and Cloud Run sessions; macOS gets a new
SessionStart hook that does nothing there. With no shim present, PATH comes out
as before. A revert is safe.
Review time: ~10 min

1. [check] `images/shared/profile-gh-shim.sh` (new): moves `ccr/bin` to the
   front when `gh` there is executable, drops other copies. Pure shell, no awk:
   it runs from a profile before PATH may reach any tools.
2. [check] `images/shared/claude/hooks/session-profile.sh` (new) and
   `images/shared/claude/settings.json`: the env-file line, moved out of the
   cloud hook. Skips an image without `/etc/profile.d/agent-images.sh` (macOS).
   Also fixes a #67 bug: it grepped for the raw path but wrote it `%q`-escaped,
   so a path with a space was appended again on every resume.
3. [check] `images/cloud/runner/merge-settings.sh` (new) and the Dockerfile:
   `jq '.[0] * .[1]'` replaced arrays, so the shared SessionStart hook would
   have been dropped on Cloud Run. Hook lists now concatenate, shared first.
4. [skim] `images/cloud/runner/profile.sh`, `images/android/files/profile.sh`:
   both source the helper last. The cloud profile's own shim code goes.
5. [skim] `images/cloud/runner/claude/hooks/session-start.sh`,
   `tests/unit/test_session_start.py`: the env-file profile line and its test
   move out; expectations go back to their pre-#67 form.
6. [skim] `images/android/scripts/setup-user.sh`, Dockerfile: install the
   helper at `/etc/agent-images/profile-gh-shim.sh`.
7. [skim] `tests/unit/test_gh_shim.py` (new), `tests/unit/test_entrypoint.py`.
8. [skim] `images/android/files/claude/CLAUDE.md`: #67's "`gh` needs no
   sign-in" note.

## Validation

- `tests/unit/test_gh_shim.py`: the helper, the Android profile, the hook, the
  merge (real repo files and synthetic ones), and a replay of an Android Bash
  tool command: login PATH from the profile without a session, Claude Code's
  shim prepended, the snapshot's `export PATH=` dropping it, then the env file
  the hook wrote. `gh` and a nested `bash -c gh` resolve to the shim, ahead of
  a signed-out `gh` in `~/bin`.
- Each guard removed fails its test: the helper source in either profile, the
  hook's once-guard, its no-profile guard, its write, the merge's
  concatenation, the helper's dedupe and executable check, and the shared
  SessionStart registration.
- `ProfileTests`, `test_session_start`: pass.
- Full unit suite: 365 run, 8 fail in `test_lock_watch`, the same 8 that fail on
  `main`.
- shellcheck and shfmt on the changed scripts: pass.
- `make lint` / `cloud-lint`: not run, `packer` isn't on this runner.
- Not run: a Docker build of the cloud image (no Docker here) and a live Android
  session. Only a live session confirms that the Android runner sets
  `CLAUDE_CONFIG_DIR` in Bash commands and writes the shim before the first
  one, as it does on Cloud Run.

## Rollback

Revert the squash commit. The next image builds restore #67's layout.

<details>
<summary>[check] Control flow: PATH for an Android Bash tool command</summary>

| Step | Before | After |
| --- | --- | --- |
| systemd starts the runner via `bash -l` | Homebrew first, no shim (no session yet) | same |
| Claude Code starts bash | `ccr/bin` first | same |
| Snapshot `export PATH=` | `ccr/bin` gone | `ccr/bin` gone |
| `CLAUDE_ENV_FILE` | none (no SessionStart hook) | sources `profile.sh`, whose helper puts `ccr/bin` first |
| `gh` resolves to | Homebrew, signed out | the shim, via the relay |
| nested `bash -c` | inherits | inherits the shim first |
| nested `bash -l` | Homebrew first | the helper puts the shim back first |

</details>

<details>
<summary>Decisions</summary>

- One shared helper rather than a copy in the Android profile (AGENTS.md "One
  source per fact"). The two profiles still differ otherwise: the cloud one
  dedupes its front, Android's prepends on every source. Unifying them is out
  of scope.
- The env-file line is a shared hook rather than an Android one: Android
  installs the shared `settings.json` as is, so an Android-only hook would have
  needed its own settings merge anyway.
- `scripts/anthropic/setup.sh` keeps its own env-file hook: it's generated
  inline for Anthropic-hosted environments, which have no shim.
- macOS: the new hook runs and writes nothing (no `/etc/profile.d` profile).
  Whether macOS sessions hide the shim the same way (`~/.zprofile` puts
  Homebrew first) wasn't checked.

</details>
