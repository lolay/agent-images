#!/bin/bash
# The native toolset's smoke test (specs/gcp.md, m4): cmake configures a C hello
# for ninja, ninja builds it with core's cc, and the binary runs and prints its
# greeting.
set -euo pipefail

cat >hello.c <<'C'
#include <stdio.h>

int main(void) {
	puts("hello from native");
	return 0;
}
C
cat >CMakeLists.txt <<'CMAKE'
cmake_minimum_required(VERSION 3.10)
project(hello C)
add_executable(hello hello.c)
CMAKE
# Their output goes to a log, shown only if a step fails (ninja prints compiler
# errors on stdout).
if ! { cmake -S . -B build -G Ninja && ninja -C build; } >build.log 2>&1; then
	tail -n 20 build.log
	echo "native: the cmake and ninja build failed"
	exit 1
fi
out="$(build/hello)"
[[ "$out" == "hello from native" ]] || {
	echo "native: build/hello printed '$out', expected 'hello from native'"
	exit 1
}
echo "native: cmake and ninja built a C hello, and it ran"
