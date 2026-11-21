#!/bin/bash
# install-lint-tools.sh <bin dir>: installs shellcheck and shfmt at the versions
# in scripts/ci/tools.env, from each project's GitHub release for this OS and
# CPU, into <bin dir>; the workflow then puts <bin dir> first on PATH.
#
# ci.yml (macOS, make lint) and cloud-build.yml (Ubuntu, make cloud-lint) both
# lint CLOUD_SCRIPTS, so they must run the same versions: Ubuntu's packages and
# Homebrew's move on their own schedules, and a formatting change in one shfmt
# would pass one gate and fail the other (r1.f17). Neither project publishes
# checksums, so the version check at the end catches a wrong asset, not a
# tampered one; the download is over TLS from the project's own releases.
set -euo pipefail

bin="${1:?usage: install-lint-tools.sh <bin dir>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ci/tools.env
source "$here/tools.env"
: "${SHELLCHECK_RELEASES:=https://github.com/koalaman/shellcheck/releases/download}"
: "${SHFMT_RELEASES:=https://github.com/mvdan/sh/releases/download}"

# Each project's name for this platform in its asset names.
case "$(uname -s)/$(uname -m)" in
Linux/x86_64) shellcheck_platform=linux.x86_64 shfmt_platform=linux_amd64 ;;
Linux/aarch64) shellcheck_platform=linux.aarch64 shfmt_platform=linux_arm64 ;;
Darwin/arm64) shellcheck_platform=darwin.aarch64 shfmt_platform=darwin_arm64 ;;
Darwin/x86_64) shellcheck_platform=darwin.x86_64 shfmt_platform=darwin_amd64 ;;
*)
	printf 'install-lint-tools.sh: no shellcheck or shfmt release for %s\n' "$(uname -sm)" >&2
	exit 1
	;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$bin"

curl -fsSL --retry 3 -o "$work/shellcheck.tar.gz" \
	"$SHELLCHECK_RELEASES/v$SHELLCHECK_VERSION/shellcheck-v$SHELLCHECK_VERSION.$shellcheck_platform.tar.gz"
tar -xzf "$work/shellcheck.tar.gz" -C "$work"
install -m 755 "$work/shellcheck-v$SHELLCHECK_VERSION/shellcheck" "$bin/shellcheck"

curl -fsSL --retry 3 -o "$work/shfmt" \
	"$SHFMT_RELEASES/v$SHFMT_VERSION/shfmt_v${SHFMT_VERSION}_$shfmt_platform"
install -m 755 "$work/shfmt" "$bin/shfmt"

# What each binary says it is, so a wrong asset fails here rather than as a lint
# difference between the two workflows.
shellcheck_reported="$("$bin/shellcheck" --version)"
if ! grep -qx "version: $SHELLCHECK_VERSION" <<<"$shellcheck_reported"; then
	printf 'install-lint-tools.sh: %s/shellcheck is not %s:\n%s\n' "$bin" "$SHELLCHECK_VERSION" "$shellcheck_reported" >&2
	exit 1
fi
shfmt_reported="$("$bin/shfmt" --version)"
if [[ "${shfmt_reported#v}" != "$SHFMT_VERSION" ]]; then
	printf 'install-lint-tools.sh: %s/shfmt is %s, not %s\n' "$bin" "$shfmt_reported" "$SHFMT_VERSION" >&2
	exit 1
fi
printf 'shellcheck %s and shfmt %s in %s\n' "$SHELLCHECK_VERSION" "$SHFMT_VERSION" "$bin"
