# Agent instructions

Instructions for coding agents working in this repo. The design is in `specs/`, the
make targets in `Makefile.md`.

## make doctor must pass

Run `make doctor` before any build, lint or test, and get it passing: run the commands
it prints under "To fix, run:". Don't work around a failing doctor because the part
you're touching doesn't seem to need the missing tool; its profiles check only what
their targets need, so a failure means a target you'll run fails too. If a fix
command fails, stop and report it.

Then run `make pre-commit` before you push. In a cloud session it runs `cloud-lint`
and `cloud-test`, what a session can run of the pull request check. A change to
`images/android/gce` takes `MODE=android` on both, `make doctor MODE=android` and
`make pre-commit MODE=android`, which adds Packer (the linux image doesn't ship it)
and the template's Packer checks.

## No Docker in a session

A cloud session can't run Docker and can't install it: Cloud Run sets
no-new-privileges, so there's no root, and rootless Docker or Podman can't map user
namespaces without their setuid helpers. Don't try. What needs it is CI's:

- Building and smoking an image: `cloud-build.yml` does it on the pull request for each
  type the change touches, and its `gate` check is the result.
- `versions.lock`: don't regenerate or hand-edit it. `lock-watch.yml` re-locks every
  type from `main` and opens its own pull request when one moved (specs/gcp.md, "The
  lock"). Until then the pull request's build reads the old lock: a new apt package
  installs from the locked snapshot, and a toolchain with no lock line takes its
  `install.sh` default.

A new runner type has no lock, and the watch only resolves types on `main`. Don't
write one: cloud-build resolves it on the pull request (`scripts/cloud/new-lock.sh`),
uploads it as the artifact `versions-lock-<type>`, and fails that type's build with
the command to fetch it in the job summary. Run that command (`gh run download <run
id> --repo lolay/agent-images --name versions-lock-<type> --dir
images/cloud/<type>`), commit the file unedited at `images/cloud/<type>/versions.lock`,
and push; the rerun builds from it. A type that already has a lock is never
re-resolved there.

In your report, don't list the build or the lock as something you couldn't do. Say the
image build runs in cloud-build on the pull request, and if one is open, give its gate
result (`gh pr checks`).

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

When a change moves a fact to its one source, grep for the old value and fix every
doc and comment that states it (specs, README.md, Makefile.md, script headers) in the
same pull request; the reviews keep finding them left behind (r4.f6, f7, f8; r5.f7,
f8, f9). A guard against a second copy matches the copy, not one spelling of it: r5
found a `releases/latest` URL and a hand-written log prefix that #133's and #126's
guards couldn't see (r5.f14, f15).

## Test fakes

A test fake rejects what the real API would reject: a malformed body, a failed
operation, an unknown flag. A fake that accepts anything lets a broken error path
pass every test (r1.f10, f11, f14; r2.f4, an unknown subcommand).

A fake has to be the command that runs. `install-toolset.sh` puts Homebrew's bin
first on `PATH` wherever Homebrew is installed (CI's runners, sessions), so a test
that drives a toolset's `install.sh` through it reaches Homebrew's `curl`, not the
fake one in front of it, and calls the real service. Put the fake bin back in
front inside the script under test, or source `install-toolset.sh` for its helpers
instead of running it. Faking `apt-get` or `sudo` is safe, since Homebrew doesn't
ship them (#133).

## Tests that can fail

A test for a guard fails when the guard is gone. Before landing one, delete or
invert the guard and watch the test go red: a test that stays green checks nothing.
r2 found four that stayed green (r2.f1, f2, f3, f6).

A test of a rule every runner type follows reads every type (`infra/gcp/*.env`,
`images/cloud/*/Dockerfile`, and every script a Dockerfile runs), never one named
type, so a new type is covered the day it lands. r3.f14's guard missed the 3d-gpu
builder stage (r4.f1), and only linux-gpu's env file was deployed in a test (r4.f2).
