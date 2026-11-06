#!/bin/bash
# Provisions the agent-android Compute Engine image in Packer's build VM
# (images/android/gce/build.pkr.hcl): the Compute Engine counterpart of
# scripts/linux/build-image.sh, running the same provisioners from the same
# staged layout. Runs as root (Packer's sudo); the provisioners run as the guest
# user, as they do in the LXD build.
#
# Ubuntu's Compute Engine image has no ubuntu user (cloud-init makes one only on
# the cloud images LXD uses), so this creates it with passwordless sudo first.
# Then: setup-user.sh and install-packages.sh (which boots the emulator once for
# its quickboot snapshot; the build VM has nested virtualization), the Compute
# Engine guest pieces from gce/files, and cleanup so every VM from the image
# boots as a new machine. Fails the build if the provisioners wrote anything on
# stderr (green builds).
set -euo pipefail

: "${STAGING_DIR:?}" "${GUEST_USER:?}"
readonly libexec_dir=/usr/local/libexec/agent-images
readonly stderr_log=/var/tmp/agent-images-build.err

log() { printf '==> provision: %s\n' "$*"; }

[[ "$(id -u)" == 0 ]] || {
	echo "provision.sh runs as root" >&2
	exit 1
}

# Packer runs this from its own user's home (0750 on Ubuntu 24.04), and sudo -u
# keeps the working directory, so the guest user's tools would start in a
# directory they can't read; Homebrew refuses to run there.
cd /

# First-boot cloud-init holds the apt lock until it's done.
cloud-init status --wait >/dev/null 2>&1 || true

if ! id "$GUEST_USER" >/dev/null 2>&1; then
	log "creating $GUEST_USER"
	useradd --create-home --shell /bin/bash --user-group --groups adm,sudo "$GUEST_USER"
fi
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$GUEST_USER" >/etc/sudoers.d/90-agent-images
chmod 440 /etc/sudoers.d/90-agent-images
visudo -cq

chown -R "$GUEST_USER:$GUEST_USER" "$STAGING_DIR"
install -m 666 /dev/null "$stderr_log"

for provisioner in setup-user install-packages; do
	log "running $provisioner as $GUEST_USER"
	sudo -u "$GUEST_USER" -H env STAGING_DIR="$STAGING_DIR" GUEST_USER="$GUEST_USER" \
		bash "$STAGING_DIR/scripts/$provisioner.sh" 2> >(tee -a "$stderr_log" >&2)
done

log "installing the Compute Engine guest pieces"
install -d -m 755 "$libexec_dir" /etc/systemd/journald.conf.d
install -m 755 "$STAGING_DIR/gce/agent-gce-order.sh" "$libexec_dir/agent-gce-order"
install -m 755 "$STAGING_DIR/gce/agent-gce-smoke.sh" "$libexec_dir/agent-gce-smoke"
install -m 644 "$STAGING_DIR/gce/agent-gce-order.service" /etc/systemd/system/agent-gce-order.service
install -m 644 "$STAGING_DIR/gce/journald-serial.conf" /etc/systemd/journald.conf.d/agent-images-serial.conf
install -m 644 "$STAGING_DIR/gce/profile-gce.sh" /etc/profile.d/agent-images-gce.sh
install -d -m 755 -o "$GUEST_USER" -g "$GUEST_USER" /scratch
systemctl daemon-reload
systemctl enable --quiet agent-gce-order.service

log "cleaning up"
# Staging copies shouldn't outlive the build. Cleared cloud-init state and
# machine-id make each VM a new machine; the build user's key goes too (the
# guest agent manages users from metadata, and runner VMs get no keys).
rm -rf "$STAGING_DIR"
apt-get clean
cloud-init clean --logs --machine-id
find /home -path '*/.ssh/authorized_keys' -delete

if [[ -s "$stderr_log" ]]; then
	log "not green: the provisioners wrote $(wc -l <"$stderr_log") stderr lines:"
	cat "$stderr_log"
	exit 1
fi
rm -f "$stderr_log"
log "green: no stderr from the provisioners"
