from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from queue import Empty, Queue
import socket
import struct
import tempfile
from threading import Event, Lock, Thread
import time
import unittest

from deskhelm_codex_adapter import CodexTuiProxy, UnixWebSocketTransport
from deskhelm_codex_adapter.app_server_transport import WEBSOCKET_GUID


class _FakeUpstreamServer:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.ready = Event()
        self.connected = Event()
        self.failure: list[BaseException] = []
        self._listener: socket.socket | None = None
        self._connections: list[socket.socket] = []
        self._connections_lock = Lock()
        self._send_lock = Lock()
        self._worker = Thread(target=self._accept, daemon=True)

    def start(self) -> None:
        self._worker.start()
        if not self.ready.wait(timeout=1):
            raise AssertionError("fake upstream did not start")

    def send(self, value: dict, connection_index: int = 0) -> None:
        connection = self._require_connection(connection_index)
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        with self._send_lock:
            connection.sendall(_server_frame(payload))

    def send_many(self, values: list[dict]) -> None:
        connection = self._require_connection()
        frames = bytearray()
        for value in values:
            payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
            frames.extend(_server_frame(payload))
        with self._send_lock:
            connection.sendall(frames)

    def receive(
        self, timeout_seconds: float = 1.0, connection_index: int = 0
    ) -> dict | None:
        connection = self._require_connection(connection_index)
        connection.settimeout(timeout_seconds)
        try:
            return _read_client_json(connection)
        except TimeoutError:
            return None

    def close(self) -> None:
        with self._connections_lock:
            connections = tuple(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        listener = self._listener
        self._listener = None
        if listener is not None:
            listener.close()
        self._worker.join(timeout=1)
        self.socket_path.unlink(missing_ok=True)

    def _accept(self) -> None:
        try:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener = listener
            listener.bind(str(self.socket_path))
            listener.listen(2)
            self.ready.set()
            while True:
                connection, _ = listener.accept()
                header = _read_until(connection, b"\r\n\r\n")
                key = _header_value(header, "Sec-WebSocket-Key")
                accept = base64.b64encode(
                    hashlib.sha1(
                        (key + WEBSOCKET_GUID).encode("ascii")
                    ).digest()
                ).decode("ascii")
                connection.sendall(
                    (
                        "HTTP/1.1 101 Switching Protocols\r\n"
                        "Upgrade: websocket\r\n"
                        "Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {accept}\r\n"
                        "\r\n"
                    ).encode("ascii")
                )
                with self._connections_lock:
                    self._connections.append(connection)
                self.connected.set()
        except BaseException as error:
            if self._listener is not None:
                self.failure.append(error)
                self.connected.set()

    def _require_connection(self, connection_index: int = 0) -> socket.socket:
        if not self.connected.wait(timeout=1):
            raise AssertionError("proxy did not connect upstream")
        if self.failure:
            raise AssertionError("fake upstream failed") from self.failure[0]
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with self._connections_lock:
                if len(self._connections) > connection_index:
                    return self._connections[connection_index]
            time.sleep(0.01)
        with self._connections_lock:
            connection_count = len(self._connections)
        if connection_count <= connection_index:
            raise AssertionError("fake upstream has no connection")
        raise AssertionError("fake upstream connection index is invalid")


class CodexTuiProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.upstream = _FakeUpstreamServer(root / "upstream.sock")
        self.upstream.start()
        self.requests: Queue = Queue()
        self.resolved: Queue[str] = Queue()
        self.resumed: Queue[str] = Queue()
        self.proxy = CodexTuiProxy(
            frontend_socket_path=root / "frontend.sock",
            upstream_socket_path=self.upstream.socket_path,
            connect_timeout_seconds=1,
        )
        self.proxy.start(
            self.requests.put,
            self.resolved.put,
            self.resumed.put,
        )
        self.tui = UnixWebSocketTransport(
            self.proxy.frontend_socket_path,
            connect_timeout_seconds=1,
        )
        self.tui.connect()
        self.assertTrue(self.upstream.connected.wait(timeout=1))

    def tearDown(self) -> None:
        self.tui.close()
        self.proxy.close()
        self.upstream.close()
        self.temporary_directory.cleanup()

    def test_forwards_bidirectionally_and_routes_voice_decision_upstream(self) -> None:
        initialize = {"method": "initialize", "id": 1, "params": {}}
        self.tui.send_message(initialize)
        self.assertEqual(self.upstream.receive(), initialize)
        response = {"id": 1, "result": {"userAgent": "test"}}
        self.upstream.send(response)
        self.assertEqual(self.tui.receive_message(1), response)

        approval = _approval("approval-1")
        self.upstream.send(approval)
        self.assertEqual(self.tui.receive_message(1), approval)
        request = self.requests.get(timeout=1)

        self.proxy.resolve_approval(request.token, "accept")

        self.assertEqual(
            self.upstream.receive(),
            {"id": "approval-1", "result": {"decision": "accept"}},
        )

    def test_auxiliary_session_picker_connection_keeps_primary_approvals_owned(self) -> None:
        picker = UnixWebSocketTransport(
            self.proxy.frontend_socket_path,
            connect_timeout_seconds=1,
        )
        self.addCleanup(picker.close)
        picker.connect()

        picker_request = {"method": "thread/list", "id": 2, "params": {}}
        picker.send_message(picker_request)
        self.assertEqual(
            self.upstream.receive(connection_index=1),
            picker_request,
        )
        picker_approval = _approval("picker-approval")
        self.upstream.send(picker_approval, connection_index=1)
        self.assertEqual(picker.receive_message(1), picker_approval)
        with self.assertRaises(Empty):
            self.requests.get(timeout=0.05)

        primary_approval = _approval("primary-approval")
        self.upstream.send(primary_approval)
        self.assertEqual(self.tui.receive_message(1), primary_approval)
        request = self.requests.get(timeout=1)
        self.assertEqual(request.rpc_id, "primary-approval")
        self.proxy.resolve_approval(request.token, "accept")
        self.assertEqual(
            self.upstream.receive(),
            {"id": "primary-approval", "result": {"decision": "accept"}},
        )

    def test_auxiliary_session_picker_reports_the_confirmed_thread(self) -> None:
        picker = UnixWebSocketTransport(
            self.proxy.frontend_socket_path,
            connect_timeout_seconds=1,
        )
        self.addCleanup(picker.close)
        picker.connect()
        resume = {
            "method": "thread/resume",
            "id": "picker-resume",
            "params": {"threadId": "thread-resumed"},
        }
        picker.send_message(resume)
        self.assertEqual(self.upstream.receive(connection_index=1), resume)
        response = {
            "id": "picker-resume",
            "result": {"thread": {"id": "thread-resumed"}},
        }
        self.upstream.send(response, connection_index=1)

        self.assertEqual(picker.receive_message(1), response)
        self.assertEqual(self.resumed.get(timeout=1), "thread-resumed")

    def test_permissions_approval_grants_exact_subset_for_one_turn(self) -> None:
        permissions = {
            "network": {"enabled": True},
            "fileSystem": {
                "entries": [
                    {
                        "access": "write",
                        "path": {
                            "type": "special",
                            "value": {"kind": "project_roots", "subpath": None},
                        },
                    }
                ]
            },
        }
        approval = _permissions_approval("permissions-accept", permissions)
        self.upstream.send(approval)
        self.assertEqual(self.tui.receive_message(1), approval)
        request = self.requests.get(timeout=1)

        self.proxy.resolve_approval(request.token, "accept")

        self.assertEqual(
            self.upstream.receive(),
            {
                "id": "permissions-accept",
                "result": {"permissions": permissions, "scope": "turn"},
            },
        )

        rejected = _permissions_approval(
            "permissions-decline",
            {"network": {"enabled": True}},
        )
        self.upstream.send(rejected)
        self.assertEqual(self.tui.receive_message(1), rejected)
        rejected_request = self.requests.get(timeout=1)

        self.proxy.resolve_approval(rejected_request.token, "decline")

        self.assertEqual(
            self.upstream.receive(),
            {
                "id": "permissions-decline",
                "result": {"permissions": {}, "scope": "turn"},
            },
        )

    def test_keyboard_decision_clears_proxy_approval(self) -> None:
        approval = _approval("approval-keyboard")
        self.upstream.send(approval)
        self.assertEqual(self.tui.receive_message(1), approval)
        request = self.requests.get(timeout=1)
        response = {
            "id": "approval-keyboard",
            "result": {"decision": "decline"},
        }

        self.tui.send_message(response)

        self.assertEqual(self.upstream.receive(), response)
        self.assertEqual(self.resolved.get(timeout=1), request.token)
        with self.assertRaisesRegex(RuntimeError, "no longer pending"):
            self.proxy.resolve_approval(request.token, "accept")

    def test_voice_first_suppresses_late_keyboard_response(self) -> None:
        approval = _approval("approval-race")
        self.upstream.send(approval)
        self.assertEqual(self.tui.receive_message(1), approval)
        request = self.requests.get(timeout=1)
        self.proxy.resolve_approval(request.token, "decline")
        self.assertEqual(
            self.upstream.receive(),
            {"id": "approval-race", "result": {"decision": "decline"}},
        )

        self.tui.send_message(
            {"id": "approval-race", "result": {"decision": "accept"}}
        )

        self.assertIsNone(self.upstream.receive(0.15))

    def test_resolved_voice_approval_suppresses_late_tui_response_and_reuse(self) -> None:
        approval = _approval("approval-reused")
        self.upstream.send(approval)
        self.assertEqual(self.tui.receive_message(1), approval)
        first_request = self.requests.get(timeout=1)
        self.proxy.resolve_approval(first_request.token, "accept")
        self.assertEqual(
            self.upstream.receive(),
            {"id": "approval-reused", "result": {"decision": "accept"}},
        )
        with self.assertRaises(Empty):
            self.resolved.get(timeout=0.05)
        resolved = {
            "method": "serverRequest/resolved",
            "params": {
                "threadId": "thread-manual",
                "requestId": "approval-reused",
            },
        }
        self.upstream.send(resolved)
        self.assertEqual(self.tui.receive_message(1), resolved)
        self.assertEqual(self.resolved.get(timeout=1), first_request.token)

        self.tui.send_message(
            {"id": "approval-reused", "result": {"decision": "decline"}}
        )

        self.assertIsNone(self.upstream.receive(0.15))
        with self.assertRaises(Empty):
            self.resolved.get(timeout=0.05)
        self.upstream.send(approval)
        self.assertEqual(self.tui.receive_message(1), approval)
        second_request = self.requests.get(timeout=1)
        self.assertNotEqual(second_request.token, first_request.token)
        self.proxy.resolve_approval(second_request.token, "decline")
        self.assertEqual(
            self.upstream.receive(),
            {"id": "approval-reused", "result": {"decision": "decline"}},
        )

    def test_disconnect_resolves_all_proxy_owned_approvals(self) -> None:
        approval = _approval("approval-disconnect")
        self.upstream.send(approval)
        self.assertEqual(self.tui.receive_message(1), approval)
        request = self.requests.get(timeout=1)

        self.tui.close()

        self.assertEqual(self.resolved.get(timeout=1), request.token)
        with self.assertRaisesRegex(RuntimeError, "no longer pending"):
            self.proxy.resolve_approval(request.token, "accept")

    def test_capacity_keeps_additional_approval_keyboard_actionable(self) -> None:
        self.tui.close()
        self.proxy.close()
        self.upstream.close()
        self.upstream = _FakeUpstreamServer(
            Path(self.temporary_directory.name) / "bounded-upstream.sock"
        )
        self.upstream.start()
        self.proxy = CodexTuiProxy(
            frontend_socket_path=Path(self.temporary_directory.name)
            / "bounded.sock",
            upstream_socket_path=self.upstream.socket_path,
            connect_timeout_seconds=1,
            max_pending_approvals=1,
        )
        self.proxy.start(
            self.requests.put,
            self.resolved.put,
            self.resumed.put,
        )
        self.tui = UnixWebSocketTransport(
            self.proxy.frontend_socket_path,
            connect_timeout_seconds=1,
        )
        self.tui.connect()
        self.assertTrue(self.upstream.connected.wait(timeout=1))

        first = _approval("approval-first")
        second = _approval("approval-second")
        self.upstream.send(first)
        self.assertEqual(self.tui.receive_message(1), first)
        self.requests.get(timeout=1)
        self.upstream.send(second)
        self.assertEqual(self.tui.receive_message(1), second)

        with self.assertRaises(Empty):
            self.requests.get(timeout=0.05)
        response = {
            "id": "approval-second",
            "result": {"decision": "accept"},
        }
        self.tui.send_message(response)
        self.assertEqual(self.upstream.receive(), response)

    def test_burst_notifications_apply_backpressure_without_disconnect(self) -> None:
        self.tui.close()
        self.proxy.close()
        self.upstream.close()
        self.upstream = _FakeUpstreamServer(
            Path(self.temporary_directory.name) / "burst-upstream.sock"
        )
        self.upstream.start()
        self.proxy = CodexTuiProxy(
            frontend_socket_path=Path(self.temporary_directory.name)
            / "burst-frontend.sock",
            upstream_socket_path=self.upstream.socket_path,
            connect_timeout_seconds=1,
            max_queued_events=1,
        )
        self.proxy.start(
            self.requests.put,
            self.resolved.put,
            self.resumed.put,
        )
        self.tui = UnixWebSocketTransport(
            self.proxy.frontend_socket_path,
            connect_timeout_seconds=1,
        )
        self.tui.connect()
        self.assertTrue(self.upstream.connected.wait(timeout=1))
        approval = _approval("approval-during-burst")
        self.upstream.send(approval)
        self.assertEqual(self.tui.receive_message(1), approval)
        approval_request = self.requests.get(timeout=1)
        notifications = [
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "sequence": sequence,
                    "delta": "x" * 4096,
                },
            }
            for sequence in range(128)
        ]

        send_failures: list[BaseException] = []

        def send_notifications() -> None:
            try:
                self.upstream.send_many(notifications)
            except BaseException as error:
                send_failures.append(error)

        sender = Thread(target=send_notifications, daemon=True)
        sender.start()
        time.sleep(0.05)
        self.proxy.resolve_approval(approval_request.token, "accept")

        received = [self.tui.receive_message(2) for _ in notifications]
        sender.join(timeout=2)
        self.assertFalse(sender.is_alive())
        self.assertEqual(send_failures, [])
        self.assertEqual(received, notifications)
        self.assertEqual(
            self.upstream.receive(),
            {
                "id": "approval-during-burst",
                "result": {"decision": "accept"},
            },
        )
        ping = {"method": "thread/list", "id": 91, "params": {}}
        self.tui.send_message(ping)
        self.assertEqual(self.upstream.receive(), ping)


def _approval(request_id: str) -> dict:
    return {
        "method": "item/commandExecution/requestApproval",
        "id": request_id,
        "params": {
            "threadId": "thread-manual",
            "turnId": "turn-manual",
            "itemId": "item-private",
            "command": "private command",
        },
    }


def _permissions_approval(request_id: str, permissions: dict) -> dict:
    return {
        "method": "item/permissions/requestApproval",
        "id": request_id,
        "params": {
            "threadId": "thread-manual",
            "turnId": "turn-manual",
            "itemId": "item-permissions",
            "startedAtMs": 1,
            "cwd": "/private/project",
            "reason": "private permission reason",
            "permissions": permissions,
        },
    }


def _read_until(connection: socket.socket, marker: bytes) -> bytes:
    value = bytearray()
    while marker not in value:
        chunk = connection.recv(4096)
        if not chunk:
            raise ConnectionError("connection closed")
        value.extend(chunk)
    return bytes(value)


def _header_value(header: bytes, name: str) -> str:
    prefix = name.lower() + ":"
    for line in header.decode("ascii").split("\r\n"):
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"missing {name}")


def _server_frame(payload: bytes) -> bytes:
    length = len(payload)
    if length < 126:
        header = bytes((0x81, length))
    elif length <= 0xFFFF:
        header = bytes((0x81, 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x81, 127)) + struct.pack("!Q", length)
    return header + payload


def _read_client_json(connection: socket.socket) -> dict:
    first, second = _read_exact(connection, 2)
    if first & 0x0F != 0x1 or not second & 0x80:
        raise AssertionError("client did not send a masked text frame")
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exact(connection, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(connection, 8))[0]
    mask = _read_exact(connection, 4)
    payload = _read_exact(connection, length)
    decoded = bytes(
        value ^ mask[index % 4] for index, value in enumerate(payload)
    )
    return json.loads(decoded.decode("utf-8"))


def _read_exact(connection: socket.socket, count: int) -> bytes:
    value = bytearray()
    while len(value) < count:
        chunk = connection.recv(count - len(value))
        if not chunk:
            raise ConnectionError("connection closed")
        value.extend(chunk)
    return bytes(value)


if __name__ == "__main__":
    unittest.main()
