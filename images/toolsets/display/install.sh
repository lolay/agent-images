#!/bin/bash
# The display toolset's socket directory: Xvfb, run by the image's user rather than
# root, can't create /tmp/.X11-unix itself. Run by install-toolset.sh.
set -euo pipefail

sudo install -d -m 1777 /tmp/.X11-unix
