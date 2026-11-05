"""Helpers shared by the unit tests: the repo root and fake commands on PATH."""

import os
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


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
