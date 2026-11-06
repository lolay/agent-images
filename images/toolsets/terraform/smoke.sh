#!/bin/bash
# The terraform toolset's smoke test (specs/gcp.md, m4): terraform init and
# terraform validate accept a module with no providers (nothing to download), and
# validate -json reports it valid.
set -euo pipefail

cat >main.tf <<'TF'
variable "name" {
  type    = string
  default = "world"
}

locals {
  greeting = "hello ${var.name}"
}

output "greeting" {
  value = local.greeting
}
TF
# Their output goes to a log, shown only if a step fails.
if ! { terraform init -input=false -backend=false -no-color &&
	terraform validate -no-color; } >terraform.log 2>&1; then
	tail -n 20 terraform.log
	echo "terraform: init or validate failed on a module with no providers"
	exit 1
fi
terraform validate -json >validate.json 2>&1 || true
grep -Eq '"valid": *true' validate.json || {
	cat validate.json
	echo "terraform: validate -json did not report the module valid"
	exit 1
}
echo "terraform: init and validate passed on a module with no providers, $(terraform version | sed -n 1p)"
