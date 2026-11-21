#!/bin/bash
# Builds the Linux runner image (make build on a Linux host): launch a build VM
# from Ubuntu's official image, provision it with images/android/scripts, publish
# it as agent-android-next, and only then move the agent-android alias to it, so
# runner VMs (which launch agent-android) never start from a half-built image. The
# counterpart of the macOS image's Packer build.
#
# Usage: [ALLOW_STDERR=1] scripts/linux/build-image.sh
#
# Settings (.env or environment): LINUX_BASE_IMAGE (default ubuntu:24.04),
# BUILD_CPU (8), BUILD_MEMORY_GB (16), BUILD_DISK_GB (64). The build VM needs
# nested KVM like a runner does: the android toolset boots the emulator once.
#
# Green builds only: if the provisioners write anything on stderr, the build
# prints it and stops before publishing, so agent-android stays as it was, as
# the Compute Engine build (images/android/gce/provision.sh) does. ALLOW_STDERR=1
# for one build (ALLOW_STDERR=1 make build) publishes anyway once you've read the
# lines. make build refuses ALLOW_STDERR from .env (the Makefile), which make
# exports to every target: there it would publish every bad build.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/linux/lib.sh
source "$script_dir/lib.sh"

readonly build_vm="agent-android-build"
readonly next_alias="$ANDROID_IMAGE_ALIAS-next"
readonly guest_staging="/tmp/agent-images"
# The staged layout, assembled here from images/android/staging.txt.
readonly host_staging="$AGENT_IMAGES_DIR/build/android-staging"
readonly provisioners=(setup-user install-packages)
# Every provisioner's stderr, so a build that isn't green says so.
readonly build_err="$AGENT_IMAGES_LOG_DIR/linux-build.err"
# The commit being built, for the versions report install-packages.sh writes.
agent_commit="$(git -C "$AGENT_IMAGES_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
readonly agent_commit
# Why 24.04: images/cloud/docker-bake.hcl, beside the base target's BASE_IMAGE.
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
rm -rf "$host_staging"
"$AGENT_IMAGES_DIR/images/android/stage.sh" "$host_staging"
lxc exec "$build_vm" -- mkdir -p "$guest_staging"
tar -C "$host_staging" -cf - . | lxc exec "$build_vm" --force-noninteractive -- tar -xf - --no-same-owner -C "$guest_staging"
rm -rf "$host_staging"

# The provisioners run as the guest user, as the macOS image's do, and use sudo
# for the root parts. Their stderr also goes to build_err: a green build has none.
mkdir -p "$AGENT_IMAGES_LOG_DIR"
: >"$build_err"
for provisioner in "${provisioners[@]}"; do
	log "running $provisioner as $GUEST_USER"
	guest_exec "$build_vm" env STAGING_DIR="$guest_staging" GUEST_USER="$GUEST_USER" AGENT_COMMIT="$agent_commit" \
		bash "$guest_staging/scripts/$provisioner.sh" 2> >(tee -a "$build_err" >&2)
	# tee can still be writing when the provisioner exits; the gate reads its file.
	wait "$!"
done

# The gate, before anything is published.
if [[ -s "$build_err" ]]; then
	log "not green: the provisioners wrote $(wc -l <"$build_err") stderr lines:" >&2
	cat "$build_err" >&2
	[[ "${ALLOW_STDERR:-}" == 1 ]] ||
		die "not publishing; $ANDROID_IMAGE_ALIAS stays as it was. Fix the lines above, or publish anyway: ALLOW_STDERR=1 make build"
	log "ALLOW_STDERR=1: publishing despite the stderr above" >&2
else
	log "green: no stderr from the provisioners"
fi

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
old_image="$(image_fingerprint "$ANDROID_IMAGE_ALIAS")"
[[ -n "$new_image" ]] || die "publish produced no $next_alias image"
if [[ -n "$old_image" ]]; then
	lxc image alias delete "$ANDROID_IMAGE_ALIAS"
fi
lxc image alias create "$ANDROID_IMAGE_ALIAS" "$new_image"
lxc image alias delete "$next_alias"
# Running VMs keep their copy-on-write clone; the old image is no longer needed.
if [[ -n "$old_image" && "$old_image" != "$new_image" ]]; then
	lxc image delete "$old_image" || log "couldn't delete the previous image $old_image" >&2
fi
log "built $ANDROID_IMAGE_ALIAS (${new_image:0:12}) from $base_image"
