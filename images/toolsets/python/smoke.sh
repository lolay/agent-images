#!/bin/bash
# The python toolset's smoke test (specs/gcp.md, m4): uv makes a venv on the
# installed Python (never a downloaded one), and a script run by the venv's
# interpreter shows it is inside that venv.
set -euo pipefail

uv venv --quiet --python "$(command -v python3)" .venv
out="$(
	.venv/bin/python - <<'PY'
import sys

assert sys.prefix != sys.base_prefix, "not running inside the venv"
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} {sum(range(1, 11))}")
PY
)" || {
	echo "python: the script did not run inside .venv"
	exit 1
}
version="${out% *}"
[[ "${out#* }" == 55 ]] || {
	echo "python: .venv/bin/python printed '$out', expected a version and 55"
	exit 1
}
echo "python: uv venv ran a script in .venv on Python $version"
