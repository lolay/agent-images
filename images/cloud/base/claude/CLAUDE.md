# This machine

A disposable Linux container on Google Cloud Run (Ubuntu 24.04, x86_64, no GPU, no
display) that serves this one session and is gone when it ends, so push anything
worth keeping. You're the image's `ubuntu` user, with passwordless sudo:
`sudo apt-get install` or `brew install` whatever a project needs. There's no Android
SDK or emulator here.

## Storage

- Work in `$AGENT_SCRATCH` (`/scratch`): a dedicated disk, deleted when this session
  ends. Your repo is checked out there, and `TMPDIR` and the caches (pip, npm, Go,
  Homebrew, `XDG_CACHE_HOME`) point there. Don't write large files elsewhere: the
  rest of the filesystem is in memory, and every file there takes the session's
  memory.
- Keep anything over 10 MB worth keeping in `$AGENT_OUTPUTS` (a Cloud Storage
  bucket, kept 30 days). Finish files on scratch, then copy them there; tell the user
  the `gs://` path from the start-of-session facts.
- Read shared inputs from `$AGENT_DATASETS` (read-only).
- Code still goes to git: commit and push before the session ends.

## Time limit

- The session ends at a set time, and after a stretch with no messages (the
  start-of-session notice gives both). Start your first reply with the notice lines
  from the start-of-session facts.
- Before starting work you expect to take over an hour, say whether it fits before
  the end time, and, when the notice gives the runtime's cost, roughly what it will
  cost. Commit and push well before the end.
