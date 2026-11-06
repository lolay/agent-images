#!/bin/bash
# The go toolset's smoke test (specs/gcp.md, m4): go build compiles a module with
# no dependencies (nothing to fetch: GOPROXY=off, GOTOOLCHAIN=local), and the
# binary runs and prints what main.go says.
set -euo pipefail
echo "go: deliberate failure (m4.s5 scratch commit, reverted next)"
exit 1

cat >go.mod <<'GOMOD'
module hello

go 1.20
GOMOD
cat >main.go <<'GO'
package main

import "fmt"

func main() { fmt.Println("hello from go") }
GO
go build -o hello . || {
	echo "go: go build failed on the hello module"
	exit 1
}
out="$(./hello)"
[[ "$out" == "hello from go" ]] || {
	echo "go: ./hello printed '$out', expected 'hello from go'"
	exit 1
}
echo "go: built and ran hello with $(go env GOVERSION)"
