from __future__ import annotations

import socket
import sys
import tempfile
from pathlib import Path
import unittest

from deskhelm_bridge.stdio_relay import MAX_RELAY_LINE_BYTES, StdioRelay


_PREAMBLE = """
import sys
sys.stdin.buffer.readline()
sys.stdin.buffer.readline()
sys.stdout.buffer.write(b"ready\\n")
sys.stdout.buffer.flush()
third = sys.stdin.buffer.readline()
sys.stdout.buffer.write(b"got:" + third)
sys.stdout.buffer.flush()
"""


class StdioRelayTests(unittest.TestCase):
    def test_one_client_reads_preamble_output_and_writes_a_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relay = StdioRelay(
                root / "agent.sock",
                [sys.executable, "-c", _PREAMBLE],
                root,
                (b'{"id":1}\n', b'{"id":2}\n'),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                relay.serve()
                self.assertEqual((root / "agent.sock").stat().st_mode & 0o777, 0o600)
                client.settimeout(3)
                client.connect(str(root / "agent.sock"))
                self.assertEqual(client.recv(64), b"ready\n")
                client.sendall(b"ping\n")
                self.assertEqual(client.recv(64), b"got:ping\n")
            finally:
                client.close()
                relay.close()

    def test_oversize_line_stops_the_process(self) -> None:
        script = (
            "import sys\n"
            f"sys.stdout.buffer.write(b'x' * {MAX_RELAY_LINE_BYTES + 1} + b'\\n')\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stdin.buffer.readline()\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relay = StdioRelay(
                root / "agent.sock",
                [sys.executable, "-c", script],
                root,
            )
            try:
                relay.serve()
                process = relay._process
                self.assertIsNotNone(process)
                assert process is not None
                self.assertIn(process.wait(timeout=3), (-15, -9))
            finally:
                relay.close()

    def test_invalid_command_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            StdioRelay(Path("/tmp/deskhelm-relay-unused.sock"), [], Path("/tmp"))
