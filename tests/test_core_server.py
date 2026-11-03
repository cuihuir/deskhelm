from __future__ import annotations

from pathlib import Path
import select
import socket
import tempfile
import time
from typing import Any
import unittest

from deskhelm_core.server import BridgeServer
from deskhelm_protocol import FrameDecoder, encode_frame


CAPS = {
    "version": 1,
    "prompt_while_busy": "queue",
    "interrupt": True,
    "approvals": "answerable",
    "approval_overflow": "decline",
    "user_input": "native_ui",
    "resume": False,
}
SESSION = {"agent_id": "grok", "session_id": "session-1", "project_id": "demo"}
RUNTIME = {"adapter_id": "test", "adapter_version": "1", "runtime_name": "test", "runtime_version": "1"}


class _Peer:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.decoder = FrameDecoder()
        self.pending: list[dict[str, Any]] = []

    def send(self, frame: dict[str, Any]) -> None:
        self.sock.sendall(encode_frame(frame))

    def next(self, kind: str | None = None, timeout: float = 2.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for index, frame in enumerate(self.pending):
                if kind is None or frame["type"] == kind:
                    return self.pending.pop(index)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no {kind or 'frame'} arrived")
            readable, _, _ = select.select([self.sock], [], [], remaining)
            if not readable:
                continue
            data = self.sock.recv(65536)
            if not data:
                raise AssertionError(f"connection closed while waiting for {kind}")
            self.pending.extend(self.decoder.feed(data))

    def closed(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([self.sock], [], [], deadline - time.monotonic())
            if readable:
                data = self.sock.recv(65536)
                if not data:
                    return True
                self.pending.extend(self.decoder.feed(data))
        return False

    def close(self) -> None:
        self.sock.close()


class BridgeServerTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "bridge.sock"
        self.server = BridgeServer(
            self.path, instance_id="server-test", handshake_seconds=0.3, tick_seconds=0.05
        )
        self.server.start()
        self.addCleanup(self.server.close)

    def peer(self) -> _Peer:
        peer = _Peer(self.path)
        self.addCleanup(peer.close)
        return peer

    def test_host_and_client_exchange_a_command_through_the_server(self) -> None:
        host = self.peer()
        host.send({"type": "hello", "protocol_version": 2, "role": "host", "client_id": "host"})
        host.next("hello_ok")
        host.send({"type": "session_open", "session": SESSION, "runtime": RUNTIME, "capabilities": CAPS})
        self.assertEqual(host.next("session_open_result")["status"], "opened")
        host.send({"type": "fact", "seq": 1, "kind": "ready"})

        client = self.peer()
        client.send({"type": "hello", "protocol_version": 2, "role": "client", "client_id": "web", "tier": "operator"})
        client.next("hello_ok")
        client.send({"type": "subscribe", "stream": "console"})
        client.send({"type": "subscribe", "stream": "interaction"})
        snapshot = client.next("snapshot")
        now = int(time.time() * 1000)
        client.send(
            {
                "type": "command",
                "command": {
                    "command_id": "c1",
                    "kind": "submit_prompt",
                    **SESSION,
                    "issued_by": "web",
                    "issued_at": now,
                    "expires_at": now + 60_000,
                    "idempotency_key": "c1",
                    "payload": {"text": "hello"},
                },
            }
        )
        effect = host.next("effect")
        host.send({"type": "effect_result", "effect_id": effect["effect_id"], "outcome": "accepted"})
        result = client.next("command_result")
        host.send(
            {
                "type": "fact",
                "seq": 2,
                "kind": "message",
                "meta": {"message_ref": "m1", "role": "assistant", "phase": "complete"},
                "content": {"text": "hi there"},
            }
        )
        interaction = client.next("interaction")
        host.close()
        while True:
            delta = client.next("delta")
            change = delta["changes"][0]
            if change.get("session", {}).get("lifecycle") == "disconnected":
                break

        self.assertEqual(snapshot["instance_id"], "server-test")
        self.assertEqual(snapshot["state"]["sessions"][0]["activity"], "idle")
        self.assertEqual(effect["payload"]["text"], "hello")
        self.assertEqual(result["code"], "dispatched")
        self.assertEqual(interaction["event"]["payload"]["text"], "hi there")

    def test_silent_connection_is_closed_after_the_handshake_deadline(self) -> None:
        silent = self.peer()

        error = silent.next("error", timeout=2)
        self.assertEqual(error["code"], "handshake_timeout")
        self.assertTrue(silent.closed())

    def test_invalid_frame_is_answered_and_closed(self) -> None:
        bad = self.peer()
        bad.sock.sendall(b"not json\n")

        self.assertEqual(bad.next("error")["code"], "invalid_frame")
        self.assertTrue(bad.closed())

    def test_a_second_server_cannot_take_the_same_socket(self) -> None:
        rival = BridgeServer(self.path)

        with self.assertRaisesRegex(RuntimeError, "already serves"):
            rival.start()

    def test_socket_is_private_to_the_user(self) -> None:
        self.assertEqual(self.path.stat().st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
