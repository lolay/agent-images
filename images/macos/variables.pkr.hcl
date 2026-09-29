variable "base_image" {
  type        = string
  description = "Tart base image. Pin a specific Xcode tag once you've picked one."
  default     = "ghcr.io/cirruslabs/macos-golden-gate-xcode:latest"
}

variable "cpu_count" {
  type        = number
  description = "Build-time CPUs. Run-time CPUs are set per VM by make vm-create."
  default     = 4
}

variable "memory_gb" {
  type        = number
  description = "Build-time memory. Run-time memory is set per VM by make vm-create."
  default     = 12
}

variable "disk_size_gb" {
  type        = number
  description = "Must be at least the base image's disk size (the Xcode images are large)."
  default     = 150
}

variable "agent_user" {
  type        = string
  description = "The single GUI user that auto-logs in and runs the runner. Not 'runner': Cirrus images reserve /Users/runner."
  default     = "agent"
}

variable "user_password" {
  type        = string
  description = "Password for the agent user. Set via PKR_VAR_user_password in .env."
  sensitive   = true
}
