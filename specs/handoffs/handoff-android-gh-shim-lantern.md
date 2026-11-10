# Handoff: Android gh shim (lantern)

Branch: `fix/android-gh-shim` (name was a guess; no branch was assigned). Pushed.

Done: the fix, tests (`tests/unit/test_gh_shim.py`), mutation checks, shellcheck,
shfmt, full unit suite (only the 8 known `test_lock_watch` failures). The PR
description draft is `specs/handoffs/pr-android-gh-shim.md`.

Pending question, verbatim: "Open the PR for fix/android-gh-shim with the
description in specs/handoffs/pr-android-gh-shim.md (not merged)?"

To resume: on a yes, remove both files in `specs/handoffs/` as the last commit,
then open the PR (draft text from that file), subscribe to it, and move the
harness footer above the `Assisted-by:` trailer.

Not verifiable here: a cloud Docker build, and a live Android session (does the
runner set CLAUDE_CONFIG_DIR in Bash commands and write the shim before the
first command, as on Cloud Run).
