#!/bin/bash
# Makes the agent user log in automatically at boot (replacing the base image's
# admin auto-login) and keeps the VM awake. Simulator and UI tests need a live
# GUI session. Auto-login needs FileVault off, which is the Tart default.
set -euo pipefail

: "${STAGING_DIR:?}" "${AGENT_USER:?}" "${USER_PASSWORD:?}"

if sudo fdesetup status | grep -q 'FileVault is On'; then
	echo "FileVault is on; automatic login will not work" >&2
	exit 1
fi

# Generated in the guest: Packer checks file uploads exist before any provisioner
# runs, so a host-generated file can't be uploaded in the same build.
python3 "$STAGING_DIR/make_kcpassword.py" "$STAGING_DIR/kcpassword"
sudo install -m 600 -o root -g wheel "$STAGING_DIR/kcpassword" /etc/kcpassword
sudo defaults write /Library/Preferences/com.apple.loginwindow autoLoginUser "$AGENT_USER"

sudo pmset -a sleep 0 displaysleep 0 disksleep 0
