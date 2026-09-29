variable "base_image" {
  type        = string
  description = "Tart base image. The Makefile sets it (PKR_VAR_base_image) to the newest Xcode image for the build host's macOS."
}

variable "vm_name" {
  type        = string
  description = "The VM Packer builds. The Makefile sets it (PKR_VAR_vm_name) to a staging name and renames it to the image name once the build succeeds."
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
