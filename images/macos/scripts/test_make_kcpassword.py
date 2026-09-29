import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from make_kcpassword import BLOCK_SIZE, KEY, encode

SCRIPT = Path(__file__).with_name("make_kcpassword.py")


def decode(data: bytes) -> str:
    plain = bytes(byte ^ KEY[index % len(KEY)] for index, byte in enumerate(data))
    return plain.rstrip(b"\x00").decode("utf-8")


class EncodeTests(unittest.TestCase):
    def test_round_trips_password(self):
        self.assertEqual(decode(encode("correct horse")), "correct horse")

    def test_output_is_whole_blocks(self):
        for length in range(1, 30):
            with self.subTest(length=length):
                self.assertEqual(len(encode("x" * length)) % BLOCK_SIZE, 0)

    def test_exact_block_multiple_still_gets_terminator(self):
        self.assertEqual(len(encode("x" * BLOCK_SIZE)), BLOCK_SIZE * 2)


class CliTests(unittest.TestCase):
    def test_writes_owner_only_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "nested" / "kcpassword"
            env = {**os.environ, "USER_PASSWORD": "hunter2"}
            subprocess.run([sys.executable, str(SCRIPT), str(output)], env=env, check=True)

            self.assertEqual(decode(output.read_bytes()), "hunter2")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_refuses_without_password(self):
        env = {key: value for key, value in os.environ.items() if key != "USER_PASSWORD"}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "/dev/null"], env=env, capture_output=True
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
