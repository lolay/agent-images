#!/bin/bash
# The terraform toolset's Terraform (tenv only manages versions) and tflint. Run by install-toolset.sh after the Brewfile, with Homebrew on PATH.
set -euo pipefail

# Each a version (a pin, or what the lock resolved) or latest; Terraform also
# takes a constraint tenv understands (~> 1.14). The Cloud Run build passes the
# locked versions (scripts/cloud/lock.sh); the VM images take these defaults.
terraform="${TERRAFORM_VERSION:-latest}"
tflint="${TFLINT_VERSION:-latest}"

# tenv reports progress on stderr, which a green build doesn't allow.
tenv tf install "$terraform" 2>&1
tenv tf use "$terraform" 2>&1

# tflint, from its release (Homebrew dropped it after its license change),
# checked against the release's checksums.
if [[ "$tflint" == latest ]]; then
	release=https://github.com/terraform-linters/tflint/releases/latest/download
else
	release="https://github.com/terraform-linters/tflint/releases/download/$tflint"
fi
work="$(mktemp -d)"
curl -fsSL -o "$work/tflint_linux_amd64.zip" "$release/tflint_linux_amd64.zip"
curl -fsSL -o "$work/checksums.txt" "$release/checksums.txt"
(cd "$work" && sha256sum --check --ignore-missing --quiet checksums.txt)
unzip -q -o "$work/tflint_linux_amd64.zip" tflint -d "$work"
sudo install -m 755 "$work/tflint" /usr/local/bin/tflint
rm -rf "$work"

printf '==> install-toolset: %s, %s\n' "$(CHECKPOINT_DISABLE=1 terraform version | head -n 1)" \
	"$(tflint --version | head -n 1)"
