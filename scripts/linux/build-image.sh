#!/bin/bash
# Builds the Linux runner image (make build on a Linux host): launch a build VM
# from Ubuntu's official image, provision it with images/linux/scripts, publish
# it as agent-linux-next, and only then move the agent-linux alias to it, so
# runner VMs (which launch agent-linux) never start from a half-built image. The
# counterpart of the macOS image's Packer build.
#
# Usage: scripts/linux/build-image.sh
#
# Settings (.env or environment): LINUX_BASE_IMAGE (default ubuntu:24.04),
# BUILD_CPU (8), BUILD_MEMORY_GB (16), BUILD_DISK_GB (64). The build VM needs
# nested KVM like a runner does: ensure-android-sdk.sh boots the emulator once.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/linux/lib.sh
source "$script_dir/lib.sh"

readonly build_vm="agent-linux-build"
readonly next_alias="$LINUX_IMAGE_ALIAS-next"
readonly guest_staging="/tmp/agent-images"
readonly linux_dir="$AGENT_IMAGES_DIR/images/linux"
readonly shared_dir="$AGENT_IMAGES_DIR/images/shared"
readonly provisioners=(setup-user install-packages ensure-android-sdk)
# Every provisioner's stderr, so a build that isn't green says so.
readonly build_err="$AGENT_IMAGES_LOG_DIR/linux-build.err"
base_image="$(setting LINUX_BASE_IMAGE ubuntu:24.04)"
cpu="$(setting BUILD_CPU 8)"
memory_gb="$(setting BUILD_MEMORY_GB 16)"
disk_gb="$(setting BUILD_DISK_GB 64)"
readonly base_image cpu memory_gb disk_gb

command -v lxc >/dev/null 2>&1 || die "lxc not found; run make host-setup"

cleanup() {
	if vm_exists "$build_vm"; then
		lxc delete --force "$build_vm" >/dev/null 2>&1 || true
	fi
}
trap cleanup EXIT

image_fingerprint() {
	lxc image info "$1" 2>/dev/null | sed -n -E 's/^Fingerprint: ([0-9a-f]+)$/\1/p'
}

# Host tools move when the image is rebuilt (specs/design.md, "Track the
# latest"): host-setup holds LXD's snap refreshes, and each build takes the
# newest.
if command -v snap >/dev/null 2>&1 && snap list lxd >/dev/null 2>&1; then
	log "refreshing LXD"
	# snap reports "no updates available" on stderr; it's progress, not an error.
	sudo snap refresh lxd 2>&1 || log "LXD refresh failed; building with the installed version" >&2
fi

cleanup
log "launching $build_vm from $base_image (${cpu} CPU, ${memory_gb} GiB, ${disk_gb} GiB disk)"
lxc launch "$base_image" "$build_vm" --vm \
	--config limits.cpu="$cpu" --config limits.memory="${memory_gb}GiB" \
	--device root,size="${disk_gb}GiB"
wait_for_guest "$build_vm" 300
# First-boot cloud-init holds the apt lock until it's done.
lxc exec "$build_vm" -- cloud-init status --wait >/dev/null 2>&1 || true

log "staging files"
lxc exec "$build_vm" -- mkdir -p "$guest_staging/scripts"
for source_dir in "$shared_dir" "$linux_dir/files"; do
	tar -C "$source_dir" -cf - . | lxc exec "$build_vm" --force-noninteractive -- tar -xf - --no-same-owner -C "$guest_staging"
done
tar -C "$linux_dir" -cf - packages.txt | lxc exec "$build_vm" --force-noninteractive -- tar -xf - --no-same-owner -C "$guest_staging"
tar -C "$linux_dir/scripts" -cf - . | lxc exec "$build_vm" --force-noninteractive -- tar -xf - --no-same-owner -C "$guest_staging/scripts"

# The provisioners run as the guest user, as the macOS image's do, and use sudo
# for the root parts. Their stderr also goes to build_err: a green build has none.
mkdir -p "$AGENT_IMAGES_LOG_DIR"
: >"$build_err"
for provisioner in "${provisioners[@]}"; do
	log "running $provisioner as $GUEST_USER"
	guest_exec "$build_vm" env STAGING_DIR="$guest_staging" GUEST_USER="$GUEST_USER" \
		bash "$guest_staging/scripts/$provisioner.sh" 2> >(tee -a "$build_err" >&2)
done

log "cleaning up the guest"
# Staging copies shouldn't outlive the build. A cleared machine-id and
# cloud-init state make each clone a new machine.
lxc exec "$build_vm" -- rm -rf "$guest_staging"
lxc exec "$build_vm" -- cloud-init clean --logs --machine-id
lxc stop "$build_vm"

if [[ -n "$(image_fingerprint "$next_alias")" ]]; then
	lxc image delete "$next_alias"
fi
log "publishing $next_alias"
lxc publish "$build_vm" local: --alias "$next_alias" --compression zstd \
	description="agent-images Linux runner, built $(date -u +%Y-%m-%dT%H:%M:%SZ) from $base_image"

# Only a finished build reaches the alias. A runner launching in the moment
# between delete and create fails its spawn, and the session is re-offered.
new_image="$(image_fingerprint "$next_alias")"
old_image="$(image_fingerprint "$LINUX_IMAGE_ALIAS")"
[[ -n "$new_image" ]] || die "publish produced no $next_alias image"
if [[ -n "$old_image" ]]; then
	lxc image alias delete "$LINUX_IMAGE_ALIAS"
fi
lxc image alias create "$LINUX_IMAGE_ALIAS" "$new_image"
lxc image alias delete "$next_alias"
# Running VMs keep their copy-on-write clone; the old image is no longer needed.
if [[ -n "$old_image" && "$old_image" != "$new_image" ]]; then
	lxc image delete "$old_image" || log "couldn't delete the previous image $old_image" >&2
fi
log "built $LINUX_IMAGE_ALIAS (${new_image:0:12}) from $base_image"
if [[ -s "$build_err" ]]; then
	log "not green: $(wc -l <"$build_err") stderr lines from the provisioners (see $build_err)"
else
	log "green: no stderr from the provisioners"
fi
