locals {
  guest_staging = "/tmp/agent-images"
}

source "tart-cli" "agent" {
  vm_base_name = var.base_image
  vm_name      = "agent-macos"
  cpu_count    = var.cpu_count
  memory_gb    = var.memory_gb
  disk_size_gb = var.disk_size_gb
  headless     = true

  # Cirrus base images ship with admin/admin and passwordless sudo.
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
      "${path.root}/scripts/make_kcpassword.py",
      "${path.root}/Brewfile",
      "${path.root}/files/",
    ]
    destination = "${local.guest_staging}/"
  }

  provisioner "shell" {
    environment_vars = [
      "STAGING_DIR=${local.guest_staging}",
      "AGENT_USER=${var.agent_user}",
      "USER_PASSWORD=${var.user_password}",
    ]
    scripts = [
      "${path.root}/scripts/create-user.sh",
      "${path.root}/scripts/enable-autologin.sh",
      "${path.root}/scripts/install-packages.sh",
    ]
  }

  # Staging copies (including the generated kcpassword) shouldn't outlive the build.
  provisioner "shell" {
    inline = ["rm -rf ${local.guest_staging}"]
  }
}
