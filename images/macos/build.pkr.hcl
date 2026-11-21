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
  # Only the build uses SSH; its last provisioner turns Remote Login off.
  ssh_username = "admin"
  ssh_password = "admin"
  ssh_timeout  = "300s"
}

build {
  sources = ["source.tart-cli.agent"]

  provisioner "shell" {
    inline = ["mkdir -p ${local.guest_staging}"]
  }

  # files/ holds the macOS-only pieces, ../shared/ the runner, watchdog, and
  # Claude settings the Linux image uses too; both land flat in staging.
  provisioner "file" {
    sources = [
      "${path.root}/Brewfile",
      "${path.root}/files/",
      "${path.root}/../shared/",
    ]
    destination = "${local.guest_staging}/"
  }

  # The session tools every image shares (specs/image-toolsets.md).
  provisioner "file" {
    source      = "${path.root}/../toolsets/core/Brewfile"
    destination = "${local.guest_staging}/Brewfile.core"
  }

  # install-toolset.sh, for its Brewfile tap reader (trust_taps), which
  # install-packages.sh sources.
  provisioner "file" {
    source      = "${path.root}/../toolsets/install-toolset.sh"
    destination = "${local.guest_staging}/install-toolset.sh"
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
      "${path.root}/scripts/grant-privacy.sh",
      "${path.root}/scripts/clear-notifications.sh",
    ]
  }

  # Staging copies shouldn't outlive the build.
  provisioner "shell" {
    inline = ["rm -rf ${local.guest_staging}"]
  }

  # Last: turn Remote Login (admin/admin) off for every boot after this one.
  # Nothing uses SSH after the build; the host reaches the guest with tart exec
  # (r1.f23). launchctl disable leaves the running sshd alone, so this session
  # survives and the Tart plugin can still shut the VM down over SSH, which
  # `systemsetup -setremotelogin off` would cut. The check fails the build if
  # the disable didn't take (macOS prints "disabled", older releases "true").
  provisioner "shell" {
    inline = [
      "sudo launchctl disable system/com.openssh.sshd",
      "sudo launchctl print-disabled system | grep -Eq '\"com.openssh.sshd\" => (disabled|true)'",
    ]
  }
}
