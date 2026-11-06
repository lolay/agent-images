#!/bin/bash
# The jvm toolset's smoke test (specs/gcp.md, m4): javac compiles a Hello class
# and java runs it, printing what it was given.
set -euo pipefail

cat >Hello.java <<'JAVA'
public class Hello {
	public static void main(String[] args) {
		System.out.println("hello from " + System.getProperty("java.specification.version"));
	}
}
JAVA
javac Hello.java
out="$(java -cp . Hello)"
version="$(javac -version 2>&1 | tail -n 1)"
[[ "$out" == hello\ from\ [0-9]* ]] || {
	echo "jvm: java ran Hello and printed '$out', expected 'hello from <version>'"
	exit 1
}
echo "jvm: $version compiled and ran Hello ('$out')"
