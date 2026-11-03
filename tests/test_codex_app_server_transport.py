from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import socket
import struct
import tempfile
from threading import Event, Thread
import unittest

from deskhelm_codex_adapter.app_server_transport import (
    AppServerTransportError,
    UnixWebSocketTransport,
    WEBSOCKET_GUID,
)


class CodexAppServerTransportTests(unittest.TestCase):
    def test_unix_websocket_handshake_masks_requests_and_reads_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "app-server.sock"
            ready = Event()
            received = []
            failure = []

            def serve() -> None:
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                        server.bind(str(socket_path))
                        server.listen(1)
                        ready.set()
                        connection, _ = server.accept()
                        with connection:
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
                            received.append(_read_client_json(connection))
                            _send_server_json(
                                connection,
                                {"id": 1, "result": {"ok": True}},
                            )
                except BaseException as error:
                    failure.append(error)

            worker = Thread(target=serve, daemon=True)
            worker.start()
            self.assertTrue(ready.wait(timeout=1))
            transport = UnixWebSocketTransport(socket_path)
            try:
                transport.connect()
                transport.send_message(
                    {"method": "initialize", "id": 1, "params": {}}
                )
                response = transport.receive_message(1)
            finally:
                transport.close()
                worker.join(timeout=1)

            self.assertEqual(failure, [])
            self.assertEqual(
                received,
                [{"method": "initialize", "id": 1, "params": {}}],
            )
            self.assertEqual(response, {"id": 1, "result": {"ok": True}})

    def test_outbound_message_limit_fails_before_socket_write(self) -> None:
        transport = UnixWebSocketTransport(
            Path("/tmp/unused-codex-app-server.sock"),
            max_message_bytes=8,
        )
        transport._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            with self.assertRaisesRegex(AppServerTransportError, "frame limit"):
                transport.send_message({"private": "oversized"})
        finally:
            transport.close()


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


def _send_server_json(connection: socket.socket, value: dict) -> None:
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    if len(payload) >= 126:
        raise AssertionError("test response is unexpectedly large")
    connection.sendall(bytes((0x81, len(payload))) + payload)


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
