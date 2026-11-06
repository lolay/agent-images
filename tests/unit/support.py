"""Helpers shared by the unit tests: the repo root and fake commands on PATH."""

import faulthandler
import os
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# The suite takes under a minute. If it ever hangs (a child holding a pipe open),
# print every thread's stack and fail instead of waiting forever.
faulthandler.dump_traceback_later(300, exit=True)


def isolated_git_env() -> dict:
    """The environment for git in a test's temp repo: none of the host's global or
    system config, so a host that signs commits (commit.gpgsign) can't fail a test
    (r2.f5)."""
    return {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def write_executable(path: Path, body: str) -> Path:
    """Write a bash script at path and make it executable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def base_env(home: Path, fake_bin: Path, **extra: str) -> dict:
    """A minimal environment: the fakes first on PATH, nothing inherited but PATH."""
    env = {
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "LANG": "C.UTF-8",
    }
    env.update(extra)
    return env


def read_env_file(path: Path) -> dict:
    """Parse KEY=value lines (as written by printf %q for plain values)."""
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values
