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
#
# Why every image stays on Ubuntu 24.04, though ubuntu:latest is 26.04 (Owner,
# 2026-11-20, r1.f18): the GPU types need CUDA 13.0, the version Cloud Run's
# 580.x driver runs (base-gpu, below), and NVIDIA ships no ubuntu26.04 image of
# it; linux-gpu builds from linux's own Dockerfile, so linux can't move without
# it, and one OS for every image keeps the toolsets tested on one. So they move
# to 26.04 together, when a CUDA 13.0 ubuntu26.04 image ships (or the driver
# moves to a CUDA that has one): this base, the orchestrator, the android
# Compute Engine and LXD images. Each of those points here instead of saying why.
target "base" {
  context    = "."
  dockerfile = "images/cloud/base/Dockerfile"
  target     = "base"
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

# The GPUs the GPU images build CUDA code for, as compute capabilities: the L4
# (8.9) and the RTX PRO 6000 (Blackwell, 12.0). Every CUDA build in them (COLMAP,
# gsplat, agent-cuda-probe) carries machine code (SASS) for each and PTX for the
# newest, which a newer GPU's driver compiles. Any order: oldest_first below
# sorts it. base-gpu hands it to the builds as the variables they read: CMake's
# CUDAARCHS ("89-real;120") and PyTorch's TORCH_CUDA_ARCH_LIST ("8.9;12.0+PTX");
# the gpu toolset builds nvcc's flags from CUDAARCHS. Adding a GPU here is the
# image side of a new GPU type, and it takes three more edits: an sm_ line for it
# in both toolsets' check.txt (3d's and gpu's, which check each binary for every
# GPU), and the edit to test_the_list_is_the_l4_and_blackwell
# (tests/unit/test_gpu_targets.py), the one place a test spells the list out.
# Like TAG and REGISTRY, a CUDA_CAPABILITIES in the environment replaces it. It
# must be dotted capabilities separated by spaces: anything else fails the bake
# rather than build without a GPU.
variable "CUDA_CAPABILITIES" {
  default = "8.9 12.0"
  validation {
    condition     = length(regexall("^ *[0-9]+\\.[0-9]+( +[0-9]+\\.[0-9]+)* *$", CUDA_CAPABILITIES)) == 1
    error_message = "CUDA_CAPABILITIES is compute capabilities separated by spaces, such as \"8.9 12.0\"."
  }
}

# The capabilities, oldest first, whatever order CUDA_CAPABILITIES gives them:
# both forms below put PTX on the last one, so the order is the list's meaning,
# not its spelling. Keyed by zero-padded major and minor, because sort() is
# lexicographic ("12.0" before "8.9") and values() of a map reads in key order.
# A capability listed twice fails the bake (a duplicate key).
function "oldest_first" {
  params = [caps]
  result = values({ for cap in caps : format("%04d.%04d", split(".", cap)[0], split(".", cap)[1]) => cap })
}

# CMake's form: <cc>-real (SASS only) for each, and plain <cc> (SASS and PTX)
# for the newest.
function "cmake_cuda_archs" {
  params = [caps]
  result = join(";", [for i, cap in oldest_first(caps) : "${replace(cap, ".", "")}${i < length(caps) - 1 ? "-real" : ""}"])
}

# PyTorch's form: dotted, with +PTX on the newest.
function "torch_cuda_arch_list" {
  params = [caps]
  result = "${join(";", oldest_first(caps))}+PTX"
}

# The GPU types' parent: base on CUDA's devel image, then base's gpu stage, which
# sets what every GPU image needs (the driver capabilities and the CUDA targets
# above). CUDA 13.0 to match the driver Cloud Run mounts (580.x, CUDA 13.0); a
# newer CUDA would need NVIDIA's forward-compatibility package. The newest 13.0
# patch on the mirror.
target "base-gpu" {
  inherits = ["base"]
  target   = "gpu"
  args = {
    BASE_IMAGE           = "mirror.gcr.io/nvidia/cuda:13.0.3-devel-ubuntu24.04"
    CUDAARCHS            = cmake_cuda_archs(regexall("[0-9]+\\.[0-9]+", CUDA_CAPABILITIES))
    TORCH_CUDA_ARCH_LIST = torch_cuda_arch_list(regexall("[0-9]+\\.[0-9]+", CUDA_CAPABILITIES))
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

# 3d-gpu: reconstruction on the GPU, on base-gpu: COLMAP built in its own stage,
# then native, display, 3d and gpu (images/cloud/3d-gpu).
target "3d-gpu-tools" {
  context    = "."
  dockerfile = "images/cloud/3d-gpu/Dockerfile"
  contexts = {
    agent-base = "target:base-gpu"
  }
}

target "3d-gpu" {
  inherits = ["_runner"]
  contexts = {
    agent-tools = "target:3d-gpu-tools"
  }
  args = {
    AGENT_TYPE = "3d-gpu"
  }
  tags = tags("3d-gpu")
}

target "versions-3d-gpu" {
  inherits = ["3d-gpu"]
  target   = "versions"
  tags     = []
  output   = versions_output("3d-gpu")
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
