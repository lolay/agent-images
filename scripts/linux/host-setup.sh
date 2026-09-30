#!/bin/bash
# Sets up a fresh Ubuntu 24.04 host (x86_64, VT-x or AMD-V) to build and run
# Linux runner VMs (make host-setup). Idempotent: re-run it any time; it only
# does what's missing. Uses sudo.
#
# - KVM with nested virtualization, so the Android emulator runs inside each VM
# - LXD (snap), refreshes held so it moves only at make build, with a ZFS pool
#   (copy-on-write clones, so a VM launches in seconds) and the lxdbr0 bridge
# - Claude Code (native installer), for the orchestrator
# - lingering, so the orchestrator's user unit starts at boot with no login
#
# Settings (.env or environment): LXD_POOL_GB, the ZFS pool's size (default 70%
# of the free space on /; it's a sparse file, so it only takes what VMs use).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
# shellcheck source=scripts/linux/lib.sh
source "$script_dir/lib.sh"

user="$(id -un)"
readonly user

[[ "$(uname -s)" == "Linux" ]] || die "host-setup is for the Linux host; see README for macOS"
[[ "$(uname -m)" == "x86_64" ]] || die "the Android emulator's KVM images need an x86_64 host (got $(uname -m))"
grep -Eq '(vmx|svm)' /proc/cpuinfo || die "no VT-x/AMD-V: turn on virtualization (VT-x or SVM mode) in the firmware settings"

log "installing packages"
sudo apt-get update -q
sudo apt-get install -y -q curl git jq make snapd zfsutils-linux cpu-checker

# KVM, and nesting: runner VMs run the emulator as their own KVM guest.
if [[ ! -e /dev/kvm ]]; then
	sudo modprobe kvm_intel 2>/dev/null || sudo modprobe kvm_amd
fi
for module in kvm_intel kvm_amd; do
	param="/sys/module/$module/parameters/nested"
	[[ -r "$param" ]] || continue
	if [[ "$(<"$param")" =~ ^(Y|1)$ ]]; then
		log "$module: nested virtualization on"
	else
		log "$module: turning nested virtualization on"
		printf 'options %s nested=1\n' "$module" | sudo tee "/etc/modprobe.d/agent-images-$module.conf" >/dev/null
		if ! { sudo modprobe -r "$module" && sudo modprobe "$module"; }; then
			die "couldn't reload $module; reboot and re-run make host-setup"
		fi
	fi
done

if ! snap list lxd >/dev/null 2>&1; then
	log "installing LXD"
	sudo snap install lxd
fi
# Held, so a refresh never restarts LXD under running sessions; make build
# refreshes it.
sudo snap refresh --hold lxd >/dev/null
if ! id -nG "$user" | tr ' ' '\n' | grep -qx lxd; then
	sudo usermod -aG lxd "$user"
	needs_relogin=true
fi

if [[ -z "$(sudo lxc storage list --format csv 2>/dev/null)" ]]; then
	pool_gb="$(setting LXD_POOL_GB "")"
	if [[ -z "$pool_gb" ]]; then
		pool_gb="$(($(df --output=avail -BG / | tail -n 1 | tr -dc '0-9') * 7 / 10))"
	fi
	log "initializing LXD: ZFS pool 'default' (${pool_gb} GiB), bridge lxdbr0"
	sudo lxd init --preseed <<EOF
networks:
- name: lxdbr0
  type: bridge
  config:
    ipv4.address: auto
    ipv6.address: none
storage_pools:
- name: default
  driver: zfs
  config:
    size: ${pool_gb}GiB
profiles:
- name: default
  devices:
    root:
      path: /
      pool: default
      type: disk
    eth0:
      name: eth0
      network: lxdbr0
      type: nic
EOF
else
	log "LXD already initialized ($(sudo lxc storage list --format csv | cut -d, -f1,2 | tr '\n' ' '))"
fi

if ! command -v claude >/dev/null 2>&1 && [[ ! -x "$HOME/.local/bin/claude" ]]; then
	log "installing Claude Code"
	curl -fsSL https://claude.ai/install.sh | bash
fi

if [[ "$(loginctl show-user "$user" --property=Linger --value 2>/dev/null)" != "yes" ]]; then
	log "enabling lingering for $user"
	sudo loginctl enable-linger "$user"
fi

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

command -v triage >/dev/null 2>&1 ||
	log "triage isn't installed; make doctor needs it (https://github.com/lolay/triage)" >&2
log "done"
if [[ "${needs_relogin:-false}" == "true" ]]; then
	log "added $user to the lxd group: reboot (or log out and back in) before make build" >&2
fi
