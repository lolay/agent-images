#!/bin/bash
# The node toolset's smoke test (specs/gcp.md, m4): node runs an ES module, which
# imports a built-in and prints what it computed. pnpm is covered by its binary check.
set -euo pipefail

cat >sum.mjs <<'MJS'
import { createHash } from 'node:crypto';

const squares = Array.from({ length: 10 }, (_, i) => (i + 1) ** 2);
const sum = squares.reduce((a, b) => a + b, 0);
const digest = createHash('sha256').update('node').digest('hex').slice(0, 8);
console.log(`${sum} ${digest}`);
MJS
out="$(node sum.mjs)"
[[ "$out" == "385 "* ]] || {
	echo "node: sum.mjs printed '$out', expected the sum of the first 10 squares, 385"
	exit 1
}
echo "node: ran an ES module ($out), on $(node --version)"
