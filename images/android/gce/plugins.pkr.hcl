packer {
  required_version = ">= 1.16.2"

  required_plugins {
    googlecompute = {
      version = "~> 1.2"
      source  = "github.com/hashicorp/googlecompute"
    }
  }
}
