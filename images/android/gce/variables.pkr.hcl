# The deploy sets these from infra/gcp/project.env and infra/gcp/android.env
# (PKR_VAR_*; scripts/cloud/deploy.sh vm-image-build), so the settings live in
# one place.

variable "project_id" {
  type        = string
  description = "The GCP project (PROJECT_ID)."
}

variable "zone" {
  type        = string
  description = "Where the build VM runs (ZONE)."
}

variable "network" {
  type        = string
  description = "The VPC (NETWORK), the one with Cloud NAT."
}

variable "subnetwork" {
  type        = string
  description = "The subnet (SUBNET). The build VM has no external IP: it reaches the internet through Cloud NAT and Packer reaches it through IAP."
}

variable "image_name" {
  type        = string
  description = "The image to create: agent-android-<date>-<commit>."
}

variable "image_family" {
  type        = string
  description = "Built into the staging family; the deploy moves it to agent-android once a VM from it passes the smoke checks."
  default     = "agent-android-next"
}

variable "commit" {
  type        = string
  description = "The main commit the image is built from, as an image label."
}

variable "machine_type" {
  type        = string
  description = "The build VM. An Intel series, for nested virtualization: the build boots the emulator once for its quickboot snapshot."
  default     = "n2-standard-8"
}

variable "disk_size_gb" {
  type        = number
  description = "The image's disk size, and so every runner VM's minimum boot disk (DISK_GB)."
  default     = 100
}

variable "guest_user" {
  type        = string
  description = "The user that runs the runner and its sessions, with passwordless sudo, as in the LXD image."
  default     = "ubuntu"
}

variable "versions_file" {
  type        = string
  description = "Where the build downloads the image's versions report (/etc/agent-images/versions.md); the deploy workflow reads it from there. The deploy passes an absolute path under out/versions/android."
  default     = "out/versions/android/versions.md"
}
