- Terraform through tenv, which switches to a repo's `.terraform-version` and
  downloads it if it isn't here, and tflint. Offline only: `terraform fmt`,
  `terraform validate` after `terraform init -backend=false`, and `tflint`.
