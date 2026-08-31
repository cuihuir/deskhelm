from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import time
from typing import Any, Protocol


WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HTTP_HEADER_BYTES = 64 * 1024
DEFAULT_MAX_MESSAGE_BYTES = 1 << 20


class AppServerTransport(Protocol):
    def connect(self) -> None: ...

    def send_message(self, message: dict[str, Any]) -> None: ...

    def receive_message(self, timeout_seconds: float) -> dict[str, Any] | None: ...

    def close(self) -> None: ...


class AppServerTransportError(RuntimeError):
    pass


class UnixWebSocketTransport:
    def __init__(
        self,
        socket_path: Path,
        *,
        connect_timeout_seconds: float = 2.0,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    ) -> None:
        if not isinstance(socket_path, Path) or not socket_path.is_absolute():
            raise ValueError("app-server socket path must be absolute")
        if connect_timeout_seconds <= 0:
            raise ValueError("connect timeout must be greater than zero")
        if max_message_bytes < 1:
            raise ValueError("max message bytes must be at least 1")
        self.socket_path = socket_path
        self.connect_timeout_seconds = connect_timeout_seconds
        self.max_message_bytes = max_message_bytes
        self._socket: socket.socket | None = None
        self._buffer = bytearray()

    def connect(self) -> None:
        if self._socket is not None:
            raise RuntimeError("app-server transport is already connected")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(self.connect_timeout_seconds)
            client.connect(str(self.socket_path))
            self._socket = client
            self._handshake()
        except BaseException:
            client.close()
            self._socket = None
            self._buffer.clear()
            raise

    def send_message(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            raise ValueError("app-server message must be an object")
        payload = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > self.max_message_bytes:
            raise AppServerTransportError("app-server message exceeds frame limit")
        self._send_frame(0x1, payload)

    def receive_message(self, timeout_seconds: float) -> dict[str, Any] | None:
        if timeout_seconds < 0:
            raise ValueError("receive timeout must not be negative")
        client = self._require_socket()
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            client.settimeout(remaining)
            try:
                first, second = self._read_exact(2)
            except TimeoutError:
                return None
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            if first & 0x70:
                raise AppServerTransportError("unsupported WebSocket extension")
            if not fin:
                raise AppServerTransportError("fragmented WebSocket frames are unsupported")
            if second & 0x80:
                raise AppServerTransportError("server WebSocket frame must not be masked")

            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            if length > self.max_message_bytes:
                raise AppServerTransportError("app-server frame exceeds limit")
            payload = self._read_exact(length)

            if opcode == 0x8:
                raise ConnectionError("app-server closed the WebSocket connection")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                if time.monotonic() >= deadline:
                    return None
                continue
            if opcode == 0xA:
                if time.monotonic() >= deadline:
                    return None
                continue
            if opcode != 0x1:
                raise AppServerTransportError("app-server frame must contain text")
            try:
                value = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AppServerTransportError("app-server emitted invalid JSON") from error
            if not isinstance(value, dict):
                raise AppServerTransportError("app-server message must be an object")
            return value

    def close(self) -> None:
        client = self._socket
        self._socket = None
        self._buffer.clear()
        if client is None:
            return
        try:
            client.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        client.close()

    def _handshake(self) -> None:
        client = self._require_socket()
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        client.sendall(request)

        response = bytearray()
        marker = b"\r\n\r\n"
        while marker not in response:
            chunk = client.recv(4096)
            if not chunk:
                raise ConnectionError("app-server closed during WebSocket handshake")
            response.extend(chunk)
            if len(response) > MAX_HTTP_HEADER_BYTES:
                raise AppServerTransportError("app-server handshake headers are oversized")
        header_bytes, remainder = response.split(marker, 1)
        self._buffer.extend(remainder)
        try:
            lines = header_bytes.decode("ascii").split("\r\n")
        except UnicodeDecodeError as error:
            raise AppServerTransportError("app-server handshake is not ASCII") from error
        if not lines or " 101 " not in f" {lines[0]} ":
            raise AppServerTransportError("app-server rejected WebSocket upgrade")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                raise AppServerTransportError("app-server handshake header is malformed")
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(
            hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise AppServerTransportError("app-server WebSocket accept key is invalid")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        client = self._require_socket()
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        client.sendall(header + mask + masked)

    def _read_exact(self, count: int) -> bytes:
        if count < 0:
            raise ValueError("read size must not be negative")
        client = self._require_socket()
        while len(self._buffer) < count:
            chunk = client.recv(max(4096, count - len(self._buffer)))
            if not chunk:
                raise ConnectionError("app-server WebSocket connection closed")
            self._buffer.extend(chunk)
        value = bytes(self._buffer[:count])
        del self._buffer[:count]
        return value

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise RuntimeError("app-server transport is not connected")
        return self._socket
