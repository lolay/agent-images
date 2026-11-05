# Builds the Cloud Run images (specs/gcp.md, "Images"). Run from the repo root,
# which is the build context: `docker buildx bake -f images/cloud/docker-bake.hcl
# linux` (make cloud-build). A type is two targets: <type>-tools, its Dockerfile
# (images/cloud/<type>) on agent-base, and <type>, the shared runner stage
# (images/cloud/runner) on those tools, which adds Claude Code last. Only <type>
# is tagged and pushed; base and <type>-tools are build parents, passed as named
# contexts, so their FROM lines never pull.

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

function "tags" {
  params = [name]
  result = [REGISTRY == "" ? "agent-${name}:${TAG}" : "${REGISTRY}/agent-${name}:${TAG}"]
}

group "default" {
  targets = ["linux", "gis", "orchestrator"]
}

target "base" {
  context    = "."
  dockerfile = "images/cloud/base/Dockerfile"
}

# The runner stage every type ends with; a type's target inherits it and names
# its tools.
target "_runner" {
  context    = "."
  dockerfile = "images/cloud/runner/Dockerfile"
  args = {
    CLAUDE_VERSION = CLAUDE_VERSION
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
  tags = tags("linux")
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
  tags = tags("gis")
}

target "orchestrator" {
  context    = "."
  dockerfile = "images/cloud/orchestrator/Dockerfile"
  args = {
    CLAUDE_VERSION = CLAUDE_VERSION
  }
  tags = tags("orchestrator")
}
