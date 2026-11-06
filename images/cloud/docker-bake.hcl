# Builds the Cloud Run images (specs/gcp.md, "Images"). Run from the repo root,
# which is the build context: `docker buildx bake -f images/cloud/docker-bake.hcl
# linux` (make cloud-build). A type is two targets: <type>-tools, its Dockerfile
# (images/cloud/<type>) on agent-base, and <type>, the shared runner stage
# (images/cloud/runner) on those tools, which adds Claude Code last. Only <type>
# is tagged and pushed; base and <type>-tools are build parents, passed as named
# contexts, so their FROM lines never pull. versions-<type> is the type's
# versions report alone (the runner Dockerfile's versions stage), written to
# out/versions/<type>/versions.md; the workflows build it in the same bake call
# as <type>, so it reuses the layers just built (specs/gcp.md, m4).
#
# There's no default group: the types are the BACKEND=run files in infra/gcp
# (scripts/cloud/changed-targets.sh --all), and every one needs its <type>,
# <type>-tools and versions-<type> targets here, <type> with its AGENT_TYPE
# (tests/unit/test_bake.py). make cloud-ci and the pull
# request check build that list and the orchestrator.

variable "REGISTRY" {
  # Empty for local builds (tags agent-<name>:<TAG>); the deploy passes
  # ARTIFACT_REGISTRY from infra/gcp/project.env.
  default = ""
}

variable "TAG" {
  default = "local"
}

variable "CLAUDE_VERSION" {
  # An exact version, or the installer's channel name (latest, stable).
  default = "latest"
}

variable "COMMIT" {
  # The commit the versions report names; the workflows pass the one they build.
  default = "unknown"
}

function "tags" {
  params = [name]
  result = [REGISTRY == "" ? "agent-${name}:${TAG}" : "${REGISTRY}/agent-${name}:${TAG}"]
}

function "versions_output" {
  params = [name]
  result = ["type=local,dest=out/versions/${name}"]
}

# The CPU types' base image: Docker Hub's ubuntu through Google's mirror (Docker
# Hub rate-limits anonymous pulls). The tag is the constraint; the build passes
# the digest the type's lock resolved it to (scripts/cloud/lock.sh bake-args).
target "base" {
  context    = "."
  dockerfile = "images/cloud/base/Dockerfile"
  args = {
    BASE_IMAGE = "mirror.gcr.io/library/ubuntu:24.04"
  }
}

# The runner stage every type ends with; a type's target inherits it and names
# its tools.
target "_runner" {
  context    = "."
  dockerfile = "images/cloud/runner/Dockerfile"
  target     = "runner"
  args = {
    CLAUDE_VERSION = CLAUDE_VERSION
    AGENT_COMMIT   = COMMIT
  }
}

target "linux-tools" {
  context    = "."
  dockerfile = "images/cloud/linux/Dockerfile"
  contexts = {
    agent-base = "target:base"
  }
}

target "linux" {
  inherits = ["_runner"]
  contexts = {
    agent-tools = "target:linux-tools"
  }
  args = {
    AGENT_TYPE = "linux"
  }
  tags = tags("linux")
}

target "versions-linux" {
  inherits = ["linux"]
  target   = "versions"
  tags     = []
  output   = versions_output("linux")
}

# The GPU types' parent: base on CUDA's devel image. CUDA 13.0 to match the
# driver Cloud Run mounts (580.x, CUDA 13.0); a newer CUDA would need NVIDIA's
# forward-compatibility package. The newest 13.0 patch on the mirror.
target "base-gpu" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "mirror.gcr.io/nvidia/cuda:13.0.3-devel-ubuntu24.04"
  }
}

# linux-gpu is linux plus the GPU: linux's own Dockerfile on base-gpu
# (linux-tools-gpu), then images/cloud/linux-gpu adds the gpu toolset.
target "linux-tools-gpu" {
  inherits = ["linux-tools"]
  contexts = {
    agent-base = "target:base-gpu"
  }
}

target "linux-gpu-tools" {
  context    = "."
  dockerfile = "images/cloud/linux-gpu/Dockerfile"
  contexts = {
    agent-linux = "target:linux-tools-gpu"
  }
}

target "linux-gpu" {
  inherits = ["_runner"]
  contexts = {
    agent-tools = "target:linux-gpu-tools"
  }
  args = {
    AGENT_TYPE = "linux-gpu"
  }
  tags = tags("linux-gpu")
}

target "versions-linux-gpu" {
  inherits = ["linux-gpu"]
  target   = "versions"
  tags     = []
  output   = versions_output("linux-gpu")
}

target "gis-tools" {
  context    = "."
  dockerfile = "images/cloud/gis/Dockerfile"
  contexts = {
    agent-base = "target:base"
  }
}

target "gis" {
  inherits = ["_runner"]
  contexts = {
    agent-tools = "target:gis-tools"
  }
  args = {
    AGENT_TYPE = "gis"
  }
  tags = tags("gis")
}

target "versions-gis" {
  inherits = ["gis"]
  target   = "versions"
  tags     = []
  output   = versions_output("gis")
}

target "orchestrator" {
  context    = "."
  dockerfile = "images/cloud/orchestrator/Dockerfile"
  args = {
    CLAUDE_VERSION = CLAUDE_VERSION
  }
  tags = tags("orchestrator")
}
