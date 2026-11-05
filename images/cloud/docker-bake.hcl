# Builds the Cloud Run images (specs/gcp.md, "Images"). Run from the repo root,
# which is the build context: `docker buildx bake -f images/cloud/docker-bake.hcl
# linux` (make cloud-build). agent-base is a build parent, never deployed: each
# type gets it as the agent-base context, so `FROM agent-base` never pulls.

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
  targets = ["linux"]
}

target "base" {
  context    = "."
  dockerfile = "images/cloud/base/Dockerfile"
  args = {
    CLAUDE_VERSION = CLAUDE_VERSION
  }
  tags = tags("base")
}

target "linux" {
  context    = "."
  dockerfile = "images/cloud/linux/Dockerfile"
  contexts = {
    agent-base = "target:base"
  }
  tags = tags("linux")
}
