packer {
  required_version = ">= 1.16.1"

  required_plugins {
    tart = {
      version = "~> 1.21"
      source  = "github.com/cirruslabs/tart"
    }
  }
}
