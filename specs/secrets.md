# Secrets for sessions

Status: **deferred.** agent-images gives sessions no secrets, the same as an
Anthropic-hosted environment on a Team plan. Work that needs secrets (code signing,
deploys, production API keys) stays in GitHub Actions. This page records why, what
sessions get instead, and the designs to pick from if a real need shows up.

Researched 2026-09-28 from Anthropic's cloud and self-hosted environment docs.

## Parity with Anthropic-hosted environments

| Anthropic-hosted | Meant for secrets? | agent-images equivalent |
| --- | --- | --- |
| Environment variables (`.env` on the environment) | No: "Every member's sessions in a shared environment read its variables, so don't include secrets in them" | Non-secret `KEY=value` lines in `vms/<vm>.env`. `vm-configure` writes them into `runner.env`, which `agent-runner` exports, and sessions inherit the runner's environment |
| Setup script | No | The image's Brewfile; sessions can `brew install` more, discarded with the VM |
| GitHub proxy | Git only | `--use-anthropic-git-proxy` (on by default) |
| API credentials (a key the proxy attaches; sessions never see it) | Yes | None. Pro and Max only, not Team or Enterprise, and "a self-hosted environment doesn't have API credentials" |

Self-hosted environments carry neither environment variables nor a setup script from
the admin page; tooling goes in the runner image. Anthropic's guidance for credentials
is to "mint credentials used during a session … per session from your wrapper script"
rather than put them in the image.

The only runner credential is the Claude environment secret (with the orchestrator, a
single-use work order instead); it registers the runner and isn't meant for sessions.

## If a session needs a secret later

All options assume the ephemeral VM per session, so nothing outlives the session.
The first two share one private peer repo, `lolay/agent-secrets`.

### A. One agents key

- `lolay/agent-secrets`: private, plain SOPS + age, one age key. Gary keeps the
  private key in his password manager; each run host has a copy in its Keychain
  (`make secret-set NAME=sops-age-key`).
- The host keeps a clone and mounts it read-only into each VM
  (`tart run --dir=agent-secrets:<clone>:ro`, seen at
  `/Volumes/My Shared Files/agent-secrets`); the VM never needs GitHub access to it.
- `vm-configure` pushes the key over `tart exec` stdin to
  `~/Library/Application Support/sops/age/keys.txt`, sops's macOS default.
- Scope: every session can decrypt everything the key opens. Only put in what's fine
  to lose to a misbehaving or prompt-injected session.

### A'. Per-repo files, chosen per session

Same key and mount as A, laid out as `secrets/<org>/<repo>.env.enc`. A runner
`command` hook (runs once per session, after checkout, before Claude starts) reads the
session's checkout remote and exports that repo's file, then
`exec "$CLAUDE_RUNNER_CLAUDE_BIN" "$@"`. Works with standby VMs because the choice
happens at session start. Convenience, not isolation: the one key still opens every
repo's file.

### B. Per-repo keys, chosen at spawn

The orchestrator's `spawn-runner` hook receives `CLAUDE_RUNNER_PRIMARY_REPO_URL` before
the VM exists, so it can push only that repo's age key (Keychain account
`<org>/<repo>`), and `.sops.yaml` encrypts `secrets/<org>/<repo>/*` to that key.
Real per-repo isolation, but standby VMs (`--min-idle`) boot with no session and get
no repo key, so sessions that need secrets wait for an on-demand boot. Repos added
mid-session don't get theirs.

### C. Per-session broker

A small host service. The session's `command` hook presents
`CLAUDE_CODE_SESSION_ACCESS_TOKEN`; the broker verifies it against Anthropic's JWKS,
checks the creator (`act.sub`) and repo, and returns just those values, which never
sit on the VM. Closest to GitHub Actions secrets; a service to build and run.

## Conventions if A, A', or B is built

- **Naming:** the encrypted file is the clear-text name plus `.enc`
  (`dev.env` → `dev.env.enc`). `.gitignore` is `secrets/*` then `!secrets/*.enc`.
- **`.enc` needs explicit types:** sops infers the format from the extension, and
  `sops exec-env` has no `--input-type` (sops 3.13.3), so it fails on `dev.env.enc`
  with "Could not unmarshal input data". `sops decrypt --input-type dotenv` and
  `sops exec-file` work. A helper in the image, `~/bin/agent-secrets`, would infer the
  type from the inner extension (`.env` dotenv, `.yaml`, `.json`, `.ini`, else binary).
- **Decrypt on demand, never up front:**

  | Need | Helper | Plaintext on disk |
  | --- | --- | --- |
  | One value for one command | `agent-secrets get dev.env API_KEY` | No |
  | Env vars for one command | `agent-secrets exec dev.env -- make test` (exports inside the helper, not as `env K=V` arguments, so values stay out of `ps`) | No |
  | A tool that needs a file | `agent-secrets file AuthKey.p8 -- tool --key {}` | Temp file, removed after |

  A decrypted `.env` in a working tree is easy for an agent to commit; environment
  variables on the runner leak into every session and into logs.

## Revisit when

- A repo's agent work regularly stalls for want of a dev secret, or
- Anthropic adds API credentials (or equivalent) to self-hosted environments, which
  would make this unnecessary.
