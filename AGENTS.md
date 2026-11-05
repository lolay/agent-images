# Agent instructions

Instructions for coding agents working in this repo. The design is in `specs/`, the
make targets in `Makefile.md`.

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
