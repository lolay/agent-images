# Agent instructions

Instructions for coding agents working in this repo. The design is in `specs/`, the
make targets in `Makefile.md`.

## make doctor must pass

Run `make doctor` before any build, lint or test, and get it passing: run the commands
it prints under "To fix, run:". Don't work around a failing doctor because the part
you're touching doesn't seem to need the missing tool; its profiles check only what
their targets need, so a failure means a target you'll run fails too. If a fix
command fails, stop and report it.

In a cloud session the gate is `make cloud-lint cloud-test`. A change to
`images/android/gce` also needs `make doctor MODE=android` (it adds Packer, which the
linux image doesn't ship) and `make cloud-lint-android`. CI runs all three.

## Docker images

Pull Docker Hub images through Google's mirror, `mirror.gcr.io`, never from Docker
Hub directly: Docker Hub rate-limits anonymous pulls, and agent sessions and CI hit
the limit (429 Too Many Requests). Official images go under `library/`:

- In a Dockerfile: `FROM mirror.gcr.io/library/ubuntu:24.04`, not `FROM ubuntu:24.04`.
  Other Docker Hub images keep their namespace: `mirror.gcr.io/nvidia/cuda:<tag>`.
- On the command line: `docker pull mirror.gcr.io/library/ubuntu:24.04`, and the same
  for `docker run` and `crane`.

The mirror is a pull-through cache, so digests match Docker Hub's, but a tag can trail
Docker Hub's briefly after it moves. If the mirror doesn't have an image, say so
rather than falling back to Docker Hub silently.

## One source per fact

A setting, list or default that more than one script needs lives in one helper or
file, which every script reads; don't copy it. Copies drift: r1 found six that had
(the runner settings' defaults, the toolset checks, the type list, `JAVA_HOME`, env
parsing, the Anthropic toolset list; `specs/reviews/2026-11-06-r1-full.md`).

## Test fakes

A test fake rejects what the real API would reject: a malformed body, a failed
operation, an unknown flag. A fake that accepts anything lets a broken error path
pass every test (r1.f10, f11, f14; r2.f4, an unknown subcommand).

## Tests that can fail

A test for a guard fails when the guard is gone. Before landing one, delete or
invert the guard and watch the test go red: a test that stays green checks nothing.
r2 found four that stayed green (r2.f1, f2, f3, f6).
