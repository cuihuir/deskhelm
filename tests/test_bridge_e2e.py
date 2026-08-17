import os
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from deskhelm_bridge.client import send_negotiated_event
from deskhelm_bridge.event import AgentEvent, AgentState


ROOT = Path(__file__).resolve().parents[1]
PYTHONPATH = str(ROOT / "bridge")


class BridgeEndToEndTests(unittest.TestCase):
    def test_emit_updates_bridge_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            environment = {**os.environ, "PYTHONPATH": PYTHONPATH}
            bridge = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "deskhelm_bridge",
                    "bridge",
                    "--plain",
                    "--max-events",
                    "1",
                    "--socket",
                    str(socket_path),
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 3
                while not socket_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(socket_path.exists(), "bridge socket was not created")

                emitted = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "deskhelm_bridge",
                        "emit",
                        "--socket",
                        str(socket_path),
                        "--agent-id",
                        "demo:backend",
                        "--slot",
                        "1",
                        "--state",
                        "running_tool",
                        "--label",
                        "backend",
                    ],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(emitted.returncode, 0, emitted.stderr)
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                if bridge.poll() is None:
                    bridge.terminate()
                    bridge.wait(timeout=3)

            self.assertEqual(bridge.returncode, 0, stderr)
            self.assertIn("slot=1", stdout)
            self.assertIn("state=running_tool", stdout)

    def test_stalled_connection_does_not_block_legacy_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, environment = self._start_bridge(socket_path, max_events=1)
            stalled = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                stalled.connect(str(socket_path))
                stalled.sendall(b"{")

                emitted = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "deskhelm_bridge",
                        "emit",
                        "--socket",
                        str(socket_path),
                        "--agent-id",
                        "demo:concurrent",
                        "--slot",
                        "0",
                        "--state",
                        "thinking",
                    ],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(emitted.returncode, 0, emitted.stderr)
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                stalled.close()
                self._stop_bridge(bridge)

            self.assertEqual(bridge.returncode, 0, stderr)
            self.assertIn("agent=demo:concurrent", stdout)

    def test_negotiated_publisher_receives_hello_and_sends_agent_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(socket_path, max_events=1)
            try:
                hello = send_negotiated_event(
                    AgentEvent(
                        agent_id="demo:negotiated",
                        slot=2,
                        state=AgentState.WAITING_USER,
                    ),
                    socket_path,
                    client_id="test-publisher",
                )
                self.assertEqual(hello.selected_version, 1)
                self.assertEqual(hello.accepted_capabilities, ("agent_event_v1",))
                self.assertEqual(hello.max_frame_bytes, 1024 * 1024)
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                self._stop_bridge(bridge)

            self.assertEqual(bridge.returncode, 0, stderr)
            self.assertIn("agent=demo:negotiated", stdout)
            self.assertIn("state=waiting_user", stdout)

    def test_unavailable_role_receives_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, environment = self._start_bridge(socket_path, max_events=1)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(socket_path))
                reader = client.makefile("rb")
                self._send_json(
                    client,
                    {
                        "protocol_version": 1,
                        "message_type": "client_hello",
                        "client_id": "test-subscriber",
                        "role": "subscriber",
                        "supported_versions": [1],
                        "capabilities": [],
                    },
                )
                error = json.loads(reader.readline())
                self.assertEqual(error["message_type"], "protocol_error")
                self.assertEqual(error["code"], "role_unavailable")

                emitted = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "deskhelm_bridge",
                        "emit",
                        "--socket",
                        str(socket_path),
                        "--agent-id",
                        "demo:legacy",
                        "--slot",
                        "0",
                        "--state",
                        "idle",
                    ],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(emitted.returncode, 0, emitted.stderr)
                bridge.communicate(timeout=3)
            finally:
                client.close()
                self._stop_bridge(bridge)

            self.assertEqual(bridge.returncode, 0)

    def test_legacy_connection_continues_after_invalid_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(socket_path, max_events=2)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(socket_path))
                self._send_json(
                    client,
                    {"agent_id": "legacy:first", "slot": 0, "state": "thinking"},
                )
                self._send_json(
                    client,
                    {"agent_id": "legacy:invalid", "slot": 0, "state": "unknown"},
                )
                self._send_json(
                    client,
                    {"agent_id": "legacy:second", "slot": 1, "state": "completed"},
                )
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                client.close()
                self._stop_bridge(bridge)

            self.assertEqual(bridge.returncode, 0, stderr)
            self.assertIn("agent=legacy:first", stdout)
            self.assertIn("agent=legacy:second", stdout)
            self.assertIn("error='unknown' is not a valid AgentState", stdout)

    @staticmethod
    def _send_json(client: socket.socket, value: dict[str, object]) -> None:
        client.sendall(
            (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")
        )

    @staticmethod
    def _start_bridge(
        socket_path: Path, *, max_events: int
    ) -> tuple[subprocess.Popen[str], dict[str, str]]:
        environment = {**os.environ, "PYTHONPATH": PYTHONPATH}
        bridge = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "deskhelm_bridge",
                "bridge",
                "--plain",
                "--max-events",
                str(max_events),
                "--socket",
                str(socket_path),
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 3
        while not socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not socket_path.exists():
            BridgeEndToEndTests._stop_bridge(bridge)
            raise AssertionError("bridge socket was not created")
        return bridge, environment

    @staticmethod
    def _stop_bridge(bridge: subprocess.Popen[str]) -> None:
        if bridge.poll() is None:
            bridge.terminate()
            bridge.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
