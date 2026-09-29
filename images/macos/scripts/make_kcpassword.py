"""Write a macOS kcpassword file for automatic login.

macOS reads the auto-login password from /etc/kcpassword, XOR-obfuscated with
a fixed, publicly known key. That's obfuscation, not encryption, so the output
is as sensitive as the password itself.

Usage: USER_PASSWORD=... python3 make_kcpassword.py <output-path>
The password comes from the environment so it never shows up in `ps`.
"""

import os
import sys
from pathlib import Path

KEY = bytes([0x7D, 0x89, 0x52, 0x23, 0xD2, 0xBC, 0xDD, 0xEA, 0xA3, 0xB9, 0x1F])
BLOCK_SIZE = 12
PASSWORD_ENV_VAR = "USER_PASSWORD"


def encode(password: str) -> bytes:
    raw = password.encode("utf-8")
    # loginwindow expects at least one NUL terminator, so an exact multiple of
    # the block size still gets a full block of padding.
    padding = BLOCK_SIZE - (len(raw) % BLOCK_SIZE)
    padded = raw + b"\x00" * padding
    return bytes(byte ^ KEY[index % len(KEY)] for index, byte in enumerate(padded))


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {PASSWORD_ENV_VAR}=... {sys.argv[0]} <output-path>", file=sys.stderr)
        return 2

    password = os.environ.get(PASSWORD_ENV_VAR)
    if not password:
        print(f"{PASSWORD_ENV_VAR} is not set", file=sys.stderr)
        return 2

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encode(password))
    output.chmod(0o600)
    return 0


if __name__ == "__main__":
    sys.exit(main())
