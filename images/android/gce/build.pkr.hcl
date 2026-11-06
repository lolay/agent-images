# The agent-android Compute Engine image (specs/gcp.md, "Compute Engine VM per
# session"): Ubuntu 24.04 provisioned by the same scripts as the Linux host's LXD
# image, plus the Compute Engine guest pieces in files/. Built by the deploy on
# main (.github/workflows/cloud-deploy.yml) as agent-<date>-<commit> in family
# agent-android-next; scripts/cloud/deploy.sh moves it to agent-android once a VM
# from it passes the smoke checks.

locals {
  guest_staging = "/tmp/agent-images"
  labels = {
    managed-by        = "agent-images"
    agent-images-type = "android"
    agent-images-sha  = var.commit
  }
}

source "googlecompute" "android" {
  project_id              = var.project_id
  zone                    = var.zone
  source_image_family     = "ubuntu-2404-lts-amd64"
  source_image_project_id = ["ubuntu-os-cloud"]
  machine_type            = var.machine_type
  disk_size               = var.disk_size_gb
  disk_type               = "pd-balanced"

  # The emulator needs KVM inside the VM.
  enable_nested_virtualization = true

  # No external IP and no service account: the build reaches the internet
  # through Cloud NAT, and Packer reaches the VM through IAP (the firewall lets
  # IAP's range reach port 22 on instances tagged packer only).
  network                         = var.network
  subnetwork                      = var.subnetwork
  omit_external_ip                = true
  use_internal_ip                 = true
  use_iap                         = true
  tags                            = ["packer"]
  disable_default_service_account = true
  ssh_username                    = "packer"
  ssh_timeout                     = "10m"
  # Packer's key goes in instance metadata; OS Login, if it's on for the
  # project, would ignore it.
  metadata = {
    enable-oslogin = "FALSE"
  }
  labels = local.labels

  # A hung build can't run up a bill.
  max_run_duration_in_seconds = 7200
  instance_termination_action = "DELETE"

  # Packer's wait for state changes, image creation included. Its 5 minute
  # default is shorter than capturing a 100 GB disk, so the first build that
  # got that far timed out while the image was still being made.
  state_timeout = "30m"

  image_name              = var.image_name
  image_family            = var.image_family
  image_description       = "agent-images android runner, from ${var.commit}"
  image_labels            = local.labels
  image_storage_locations = ["us-central1"]
}

build {
  sources = ["source.googlecompute.android"]

  provisioner "shell" {
    inline = ["mkdir -p ${local.guest_staging}/scripts ${local.guest_staging}/toolsets ${local.guest_staging}/gce"]
  }

  # The layout scripts/linux/build-image.sh stages in its build VM, plus gce/.
  provisioner "file" {
    sources = [
      "${path.root}/../../shared/",
      "${path.root}/../files/",
      "${path.root}/../toolsets.txt",
    ]
    destination = "${local.guest_staging}/"
  }

  provisioner "file" {
    source      = "${path.root}/../../toolsets/"
    destination = "${local.guest_staging}/toolsets/"
  }

  provisioner "file" {
    source      = "${path.root}/../scripts/"
    destination = "${local.guest_staging}/scripts/"
  }

  provisioner "file" {
    source      = "${path.root}/files/"
    destination = "${local.guest_staging}/gce/"
  }

  provisioner "shell" {
    environment_vars = [
      "STAGING_DIR=${local.guest_staging}",
      "GUEST_USER=${var.guest_user}",
    ]
    execute_command = "sudo -n env {{ .Vars }} bash '{{ .Path }}'"
    script          = "${path.root}/provision.sh"
  }
}
