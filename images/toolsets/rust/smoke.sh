#!/bin/bash
# The rust toolset's smoke test (specs/gcp.md, m4): cargo builds a crate with no
# dependencies, offline, and its binary runs and prints what it should.
set -euo pipefail

mkdir -p hello/src
cat >hello/Cargo.toml <<'TOML'
[package]
name = "hello"
version = "0.1.0"
edition = "2021"
TOML
cat >hello/src/main.rs <<'RUST'
fn main() {
    println!("hello from rust");
}
RUST
cargo build --offline --quiet --manifest-path hello/Cargo.toml >build.log 2>&1 || {
	cat build.log
	echo "rust: cargo build of the hello crate failed, output above"
	exit 1
}
out="$(hello/target/debug/hello)"
[[ "$out" == "hello from rust" ]] || {
	echo "rust: hello printed '$out', expected 'hello from rust'"
	exit 1
}
echo "rust: cargo built and ran hello, on rustc $(rustc --version | cut -d' ' -f2)"
