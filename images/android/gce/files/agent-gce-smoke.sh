#!/bin/bash
# agent-gce-smoke: the checks the deploy's smoke VM runs on a new agent-android
# image before it joins the family (scripts/cloud/deploy.sh vm-image). Run by
# agent-gce-order as the guest user through a login shell, so PATH and
# ANDROID_HOME are a session's. Installed as
# /usr/local/libexec/agent-images/agent-gce-smoke.
#
# The toolsets' binary checks and smoke tests come from the image's record, through
# agent-toolset-checks (images/toolsets/toolset-checks.sh), as in the Cloud Run
# images' smoke; what's checked here by hand is only what the VM adds: sudo, KVM,
# scratch and the emulator, whose boot is the android toolset's smoke test.
#
# Prints one line per check and key=value lines for the timings, which
# agent-gce-order publishes as guest attributes; exits 1 if any check failed.
# The emulator boots twice from the image's quickboot snapshot: the first boot
# reads a disk Compute Engine is still restoring from the image (lazily), the
# second is what a session sees once the disk is warm.
set -uo pipefail

failed=0

check() {
	local name="$1"
	shift
	local output
	if output="$("$@" 2>&1)"; then
		printf 'ok    %s: %s\n' "$name" "$(head -n 1 <<<"$output")"
	else
		printf 'FAIL  %s: %s\n' "$name" "$(tail -n 3 <<<"$output" | tr '\n' ' ')"
		failed=1
	fi
}

# boot_seconds <name>: start the emulator, wait for boot, print name=<seconds>.
boot_seconds() {
	local started ended
	started="$(date +%s.%N)"
	if agent-emulator start >/dev/null && agent-emulator wait >/dev/null; then
		ended="$(date +%s.%N)"
		printf 'ok    emulator boot (%s)\n' "$1"
		awk -v name="$1" -v a="$started" -v b="$ended" 'BEGIN { printf "%s=%.1f\n", name, b - a }'
	else
		printf 'FAIL  emulator boot (%s)\n' "$1"
		failed=1
	fi
}

check "claude" claude --version
check "sudo" sudo -n true
# runuser -l gives the guest user login.defs's PATH, which has no /usr/sbin;
# sessions get systemd's, which does.
check "kvm" /usr/sbin/kvm-ok
check "scratch writable" test -w "${AGENT_SCRATCH:-/scratch}"

# Every binary check's line, under one result.
if binaries="$(agent-toolset-checks versions 2>&1)"; then
	printf 'ok    binary checks\n'
else
	printf 'FAIL  binary checks\n'
	failed=1
fi
while IFS= read -r line; do
	printf '        %s\n' "$line"
done <<<"$binaries"
while read -r name; do
	check "smoke-$name" agent-toolset-checks smoke "$name" --brief
done < <(agent-toolset-checks smoke-list 2>/dev/null)

boot_seconds emulator-boot-sec-cold
check "api level" adb -s emulator-5554 shell getprop ro.build.version.sdk
check "abi" adb -s emulator-5554 shell getprop ro.product.cpu.abi
agent-emulator stop >/dev/null 2>&1
boot_seconds emulator-boot-sec-warm
agent-emulator stop >/dev/null 2>&1

printf 'nproc=%s\n' "$(nproc)"
exit "$failed"
