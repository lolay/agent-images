#!/bin/bash
# The proto toolset's smoke test (specs/gcp.md, m4): protoc compiles a one-message
# proto3 file to a descriptor set, and the set names the message. buf and grpcurl
# are covered by their binary checks.
set -euo pipefail

cat >hello.proto <<'PROTO'
syntax = "proto3";

package smoke;

message Hello {
	string greeting = 1;
}
PROTO
if ! protoc --descriptor_set_out=hello.pb hello.proto >protoc.log 2>&1; then
	tail -n 20 protoc.log
	echo "proto: protoc failed to compile hello.proto"
	exit 1
fi
[[ -s hello.pb ]] || {
	echo "proto: protoc wrote no descriptor set (hello.pb is missing or empty)"
	exit 1
}
# --decode_raw prints the descriptor's strings, so the message name appears quoted.
raw="$(protoc --decode_raw <hello.pb)"
[[ "$raw" == *'"Hello"'* ]] || {
	echo "proto: the descriptor set in hello.pb does not name the Hello message"
	exit 1
}
echo "proto: $(protoc --version) compiled hello.proto to a descriptor set naming Hello"
