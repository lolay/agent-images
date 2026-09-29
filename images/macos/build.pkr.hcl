locals {
  guest_staging = "/tmp/agent-images"
}

source "tart-cli" "agent" {
  vm_base_name = var.base_image
  vm_name      = var.vm_name
  cpu_count    = var.cpu_count
  memory_gb    = var.memory_gb
  disk_size_gb = var.disk_size_gb
  headless     = true

  # Cirrus base images ship with admin/admin and passwordless sudo. That user is
  # also the guest user (var.guest_user): it logs in automatically and runs the runner.
  ssh_username = "admin"
  ssh_password = "admin"
  ssh_timeout  = "300s"
}

build {
  sources = ["source.tart-cli.agent"]

  provisioner "shell" {
    inline = ["mkdir -p ${local.guest_staging}"]
  }

  provisioner "file" {
    sources = [
      "${path.root}/Brewfile",
      "${path.root}/files/",
    ]
    destination = "${local.guest_staging}/"
  }

  provisioner "shell" {
    environment_vars = [
      "STAGING_DIR=${local.guest_staging}",
      "GUEST_USER=${var.guest_user}",
    ]
    scripts = [
      "${path.root}/scripts/setup-user.sh",
      "${path.root}/scripts/install-packages.sh",
      "${path.root}/scripts/ensure-xcode.sh",
    ]
  }

  # Staging copies shouldn't outlive the build.
  provisioner "shell" {
    inline = ["rm -rf ${local.guest_staging}"]
  }
}
