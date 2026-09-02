from __future__ import annotations

import base64
from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from queue import Empty, Full, Queue
import select
import socket
import struct
from threading import Event, RLock, Thread
import time
from typing import Any, Protocol

from .app_server_transport import (
    AppServerTransport,
    AppServerTransportError,
    DEFAULT_MAX_MESSAGE_BYTES,
    MAX_HTTP_HEADER_BYTES,
    UnixWebSocketTransport,
    WEBSOCKET_GUID,
)


_COMMAND_APPROVAL_METHOD = "item/commandExecution/requestApproval"
_FILE_APPROVAL_METHOD = "item/fileChange/requestApproval"
_PERMISSIONS_APPROVAL_METHOD = "item/permissions/requestApproval"
_SERVER_REQUEST_RESOLVED = "serverRequest/resolved"
_APPROVAL_METHODS = {
    _COMMAND_APPROVAL_METHOD,
    _FILE_APPROVAL_METHOD,
    _PERMISSIONS_APPROVAL_METHOD,
}
_ACCEPT_POLL_SECONDS = 0.1
_CONNECTION_POLL_SECONDS = 0.02
_QUEUE_POLL_SECONDS = 0.1
_RESOLVED_SUPPRESSION_MILLISECONDS = 30_000
_MAX_TUI_CONNECTIONS = 2


@dataclass(frozen=True, slots=True)
class ProxiedApprovalRequest:
    token: str
    rpc_id: str | int
    method: str
    params: dict[str, Any]


class TuiApprovalProxy(Protocol):
    def start(
        self,
        on_approval_request: Callable[[ProxiedApprovalRequest], None],
        on_approval_resolved: Callable[[str], None],
    ) -> None: ...

    def resolve_approval(self, token: str, decision: str) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _PendingApproval:
    token: str
    rpc_id: str | int
    rpc_key: tuple[str, str]
    expires_at: int
    method: str
    requested_permissions: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _SuppressedApproval:
    token: str
    expires_at: int
    server_resolved: bool = False
    tui_response_seen: bool = False
    notified: bool = False


@dataclass(frozen=True, slots=True)
class _UpstreamMessage:
    value: dict[str, Any]
    track_approvals: bool


@dataclass(frozen=True, slots=True)
class _VoiceDecision:
    pending: _PendingApproval
    decision: str


@dataclass(slots=True)
class CodexTuiProxy:
    frontend_socket_path: Path
    upstream_socket_path: Path
    approval_timeout_ms: int = 300_000
    connect_timeout_seconds: float = 2.0
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    max_pending_approvals: int = 16
    max_queued_events: int = 64
    transport_factory: Callable[[], AppServerTransport] | None = None
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _stop: Event = field(default_factory=Event, init=False, repr=False)
    _listener: socket.socket | None = field(default=None, init=False, repr=False)
    _worker: Thread | None = field(default=None, init=False, repr=False)
    _frontend: _ServerWebSocketConnection | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _upstream: AppServerTransport | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _connection_stop: Event | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _events: Queue[object] | None = field(default=None, init=False, repr=False)
    _decisions: Queue[object] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _connection_generation: int = field(default=0, init=False)
    _primary_connection_generation: int = field(default=0, init=False)
    _active_connection_count: int = field(default=0, init=False)
    _next_token: int = field(default=1, init=False)
    _pending_by_token: dict[str, _PendingApproval] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _pending_by_rpc: dict[tuple[str, str], str] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _suppressed_by_rpc: dict[tuple[str, str], _SuppressedApproval] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _on_approval_request: Callable[[ProxiedApprovalRequest], None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _on_approval_resolved: Callable[[str], None] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        for path, name in (
            (self.frontend_socket_path, "frontend socket path"),
            (self.upstream_socket_path, "upstream socket path"),
        ):
            if not isinstance(path, Path) or not path.is_absolute():
                raise ValueError(f"{name} must be absolute")
        if self.frontend_socket_path == self.upstream_socket_path:
            raise ValueError("frontend and upstream sockets must differ")
        if self.approval_timeout_ms < 1:
            raise ValueError("approval timeout must be at least 1 ms")
        if self.connect_timeout_seconds <= 0:
            raise ValueError("connect timeout must be greater than zero")
        for value, name in (
            (self.max_message_bytes, "max_message_bytes"),
            (self.max_pending_approvals, "max_pending_approvals"),
            (self.max_queued_events, "max_queued_events"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.transport_factory is None:
            self.transport_factory = lambda: UnixWebSocketTransport(
                self.upstream_socket_path,
                connect_timeout_seconds=self.connect_timeout_seconds,
                max_message_bytes=self.max_message_bytes,
            )

    def start(
        self,
        on_approval_request: Callable[[ProxiedApprovalRequest], None],
        on_approval_resolved: Callable[[str], None],
    ) -> None:
        if not callable(on_approval_request) or not callable(on_approval_resolved):
            raise ValueError("proxy callbacks must be callable")
        with self._lock:
            if self._worker is not None:
                raise RuntimeError("Codex TUI proxy is already started")
            if self.frontend_socket_path.exists():
                raise ValueError("Codex TUI proxy socket already exists")
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(self.frontend_socket_path))
                self.frontend_socket_path.chmod(0o600)
                # Codex opens a short-lived second connection for /resume.
                listener.listen(_MAX_TUI_CONNECTIONS)
                listener.settimeout(_ACCEPT_POLL_SECONDS)
            except BaseException:
                listener.close()
                self.frontend_socket_path.unlink(missing_ok=True)
                raise
            self._listener = listener
            self._on_approval_request = on_approval_request
            self._on_approval_resolved = on_approval_resolved
            self._stop.clear()
            worker = Thread(
                target=self._run,
                name="deskhelm-codex-tui-proxy",
                daemon=True,
            )
            self._worker = worker
            worker.start()

    def resolve_approval(self, token: str, decision: str) -> None:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("approval token must not be empty")
        if decision not in {"accept", "decline"}:
            raise ValueError("unsupported voice approval decision")
        with self._lock:
            pending = self._pending_by_token.get(token)
            if pending is None:
                raise RuntimeError("Codex TUI approval is no longer pending")
            if (
                pending.rpc_key not in self._suppressed_by_rpc
                and len(self._suppressed_by_rpc) >= self.max_pending_approvals
            ):
                raise RuntimeError("Codex TUI approval suppression is full")
            decisions = self._decisions
            if decisions is None:
                raise RuntimeError("Codex TUI proxy is not connected")
            self._pending_by_token.pop(token, None)
            self._pending_by_rpc.pop(pending.rpc_key, None)
            self._suppressed_by_rpc[pending.rpc_key] = _SuppressedApproval(
                token=token,
                expires_at=pending.expires_at,
            )
            try:
                decisions.put_nowait(_VoiceDecision(pending, decision))
            except Full as error:
                self._suppressed_by_rpc.pop(pending.rpc_key, None)
                self._pending_by_token[token] = pending
                self._pending_by_rpc[pending.rpc_key] = token
                raise RuntimeError("Codex TUI proxy decision queue is full") from error

    def close(self) -> None:
        with self._lock:
            worker = self._worker
            if worker is None:
                self.frontend_socket_path.unlink(missing_ok=True)
                return
            self._stop.set()
            listener = self._listener
            frontend = self._frontend
            upstream = self._upstream
        if listener is not None:
            listener.close()
        if frontend is not None:
            frontend.close()
        if upstream is not None:
            upstream.close()
        worker.join(timeout=max(2.0, self.connect_timeout_seconds + 1.0))
        with self._lock:
            self._listener = None
            self._worker = None
            self._on_approval_request = None
            self._on_approval_resolved = None
        self.frontend_socket_path.unlink(missing_ok=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                if self._active_connection_count >= _MAX_TUI_CONNECTIONS:
                    connection.close()
                    continue
                self._active_connection_count += 1
                self._connection_generation += 1
                generation = self._connection_generation
                is_primary = self._primary_connection_generation == 0
                if is_primary:
                    self._primary_connection_generation = generation
            Thread(
                target=self._serve_accepted_connection,
                args=(connection, generation, is_primary),
                name="deskhelm-codex-tui-connection",
                daemon=True,
            ).start()

    def _serve_accepted_connection(
        self,
        connection: socket.socket,
        generation: int,
        is_primary: bool,
    ) -> None:
        try:
            self._serve_connection(connection, generation, is_primary)
        except Exception:
            pass
        finally:
            connection.close()
            if is_primary:
                self._clear_connection(generation)
            with self._lock:
                self._active_connection_count -= 1

    def _serve_connection(
        self,
        connection: socket.socket,
        generation: int,
        is_primary: bool,
    ) -> None:
        frontend = _ServerWebSocketConnection(
            connection,
            max_message_bytes=self.max_message_bytes,
            handshake_timeout_seconds=self.connect_timeout_seconds,
        )
        frontend.accept_handshake()
        factory = self.transport_factory
        if factory is None:
            return
        upstream = factory()
        upstream.connect()
        events: Queue[object] = Queue(maxsize=self.max_queued_events)
        decisions: Queue[object] = Queue(maxsize=self.max_pending_approvals)
        connection_stop = Event()
        with self._lock:
            if self._stop.is_set():
                upstream.close()
                return
            if is_primary:
                self._frontend = frontend
                self._upstream = upstream
                self._events = events
                self._decisions = decisions
                self._connection_stop = connection_stop
        reader = Thread(
            target=self._read_upstream,
            args=(upstream, events, connection_stop, is_primary),
            name="deskhelm-codex-tui-upstream",
            daemon=True,
        )
        reader.start()
        try:
            while not self._stop.is_set() and not connection_stop.is_set():
                processed = self._drain_events(
                    frontend,
                    upstream,
                    decisions,
                    limit=self.max_pending_approvals,
                )
                processed += self._drain_events(
                    frontend,
                    upstream,
                    events,
                    limit=32,
                )
                if frontend.has_input():
                    message = frontend.receive_message(0.1)
                    if message is not None:
                        self._handle_tui_message(
                            upstream,
                            message,
                            track_approvals=is_primary,
                        )
                elif processed == 0:
                    if not self._process_one_event(
                        frontend,
                        upstream,
                        decisions,
                    ):
                        self._process_one_event(
                            frontend,
                            upstream,
                            events,
                            timeout_seconds=_CONNECTION_POLL_SECONDS,
                        )
                if is_primary:
                    self._expire_pending()
        finally:
            connection_stop.set()
            upstream.close()
            frontend.close()
            reader.join(timeout=1.0)
            if is_primary:
                self._clear_connection(generation)

    def _read_upstream(
        self,
        upstream: AppServerTransport,
        events: Queue[object],
        connection_stop: Event,
        track_approvals: bool,
    ) -> None:
        try:
            while not self._stop.is_set() and not connection_stop.is_set():
                message = upstream.receive_message(0.1)
                if message is None:
                    continue
                event = _UpstreamMessage(message, track_approvals)
                while not self._stop.is_set() and not connection_stop.is_set():
                    try:
                        events.put(event, timeout=_QUEUE_POLL_SECONDS)
                        break
                    except Full:
                        continue
        except Exception:
            connection_stop.set()

    def _process_one_event(
        self,
        frontend: _ServerWebSocketConnection,
        upstream: AppServerTransport,
        events: Queue[object],
        *,
        timeout_seconds: float = 0.0,
    ) -> bool:
        try:
            event = events.get(timeout=timeout_seconds)
        except Empty:
            return False
        if isinstance(event, _UpstreamMessage):
            self._handle_upstream_message(
                frontend,
                event.value,
                track_approvals=event.track_approvals,
            )
        elif isinstance(event, _VoiceDecision):
            upstream.send_message(
                {
                    "id": event.pending.rpc_id,
                    "result": _approval_result(event.pending, event.decision),
                }
            )
        else:
            raise RuntimeError("Codex TUI proxy received an unknown event")
        return True

    def _drain_events(
        self,
        frontend: _ServerWebSocketConnection,
        upstream: AppServerTransport,
        events: Queue[object],
        *,
        limit: int,
    ) -> int:
        processed = 0
        for _ in range(limit):
            if not self._process_one_event(frontend, upstream, events):
                break
            processed += 1
        return processed

    def _handle_upstream_message(
        self,
        frontend: _ServerWebSocketConnection,
        message: dict[str, Any],
        *,
        track_approvals: bool,
    ) -> None:
        frontend.send_message(message)
        if not track_approvals:
            return
        method = message.get("method")
        if method == _SERVER_REQUEST_RESOLVED:
            params = message.get("params")
            if isinstance(params, dict):
                self._resolve_rpc(params.get("requestId"))
            return
        if method not in _APPROVAL_METHODS or "id" not in message:
            return
        params = message.get("params")
        if not isinstance(params, dict):
            return
        try:
            rpc_id = _rpc_id(message.get("id"))
        except ValueError:
            return
        rpc_key = _rpc_key(rpc_id)
        now_ms = int(time.time() * 1000)
        requested_permissions = None
        if method == _PERMISSIONS_APPROVAL_METHOD:
            permissions = params.get("permissions")
            if not isinstance(permissions, dict):
                return
            requested_permissions = deepcopy(permissions)
        request: ProxiedApprovalRequest | None = None
        callback = None
        with self._lock:
            suppressed = self._suppressed_by_rpc.get(rpc_key)
            if suppressed is not None:
                if not suppressed.server_resolved and suppressed.expires_at > now_ms:
                    return
                self._suppressed_by_rpc.pop(rpc_key, None)
            if rpc_key in self._pending_by_rpc:
                return
            if len(self._pending_by_token) >= self.max_pending_approvals:
                return
            token = self._new_token_locked(rpc_key)
            pending = _PendingApproval(
                token=token,
                rpc_id=rpc_id,
                rpc_key=rpc_key,
                expires_at=now_ms + self.approval_timeout_ms,
                method=method,
                requested_permissions=requested_permissions,
            )
            self._pending_by_token[token] = pending
            self._pending_by_rpc[rpc_key] = token
            request = ProxiedApprovalRequest(token, rpc_id, method, params)
            callback = self._on_approval_request
        if callback is None or request is None:
            return
        try:
            callback(request)
        except Exception:
            self._resolve_token(request.token)

    def _handle_tui_message(
        self,
        upstream: AppServerTransport,
        message: dict[str, Any],
        *,
        track_approvals: bool,
    ) -> None:
        if not track_approvals:
            upstream.send_message(message)
            return
        if "method" in message or "id" not in message:
            upstream.send_message(message)
            return
        try:
            rpc_key = _rpc_key(_rpc_id(message.get("id")))
        except ValueError:
            upstream.send_message(message)
            return
        callback_token = ""
        suppress = False
        with self._lock:
            token = self._pending_by_rpc.pop(rpc_key, "")
            if token:
                self._pending_by_token.pop(token, None)
                callback_token = token
            else:
                suppressed = self._suppressed_by_rpc.get(rpc_key)
                if suppressed is not None:
                    if suppressed.server_resolved:
                        self._suppressed_by_rpc.pop(rpc_key, None)
                    else:
                        self._suppressed_by_rpc[rpc_key] = _SuppressedApproval(
                            token=suppressed.token,
                            expires_at=suppressed.expires_at,
                            server_resolved=False,
                            tui_response_seen=True,
                            notified=suppressed.notified,
                        )
                    suppress = True
        if suppress:
            return
        try:
            upstream.send_message(message)
        finally:
            if callback_token:
                self._notify_resolved(callback_token)

    def _resolve_rpc(self, rpc_id_value: object) -> None:
        try:
            rpc_key = _rpc_key(_rpc_id(rpc_id_value))
        except ValueError:
            return
        token = ""
        suppressed_token = ""
        with self._lock:
            token = self._pending_by_rpc.pop(rpc_key, "")
            if token:
                self._pending_by_token.pop(token, None)
            suppressed = self._suppressed_by_rpc.get(rpc_key)
            if suppressed is not None:
                if not suppressed.notified:
                    suppressed_token = suppressed.token
                if suppressed.tui_response_seen:
                    self._suppressed_by_rpc.pop(rpc_key, None)
                else:
                    self._suppressed_by_rpc[rpc_key] = _SuppressedApproval(
                        token=suppressed.token,
                        expires_at=min(
                            suppressed.expires_at,
                            int(time.time() * 1000)
                            + _RESOLVED_SUPPRESSION_MILLISECONDS,
                        ),
                        server_resolved=True,
                        tui_response_seen=False,
                        notified=True,
                    )
        if token:
            self._notify_resolved(token)
        if suppressed_token:
            self._notify_resolved(suppressed_token)

    def _resolve_token(self, token: str) -> None:
        with self._lock:
            pending = self._pending_by_token.pop(token, None)
            if pending is None:
                return
            self._pending_by_rpc.pop(pending.rpc_key, None)
        self._notify_resolved(token)

    def _expire_pending(self) -> None:
        now_ms = int(time.time() * 1000)
        expired_suppression_tokens: tuple[str, ...] = ()
        with self._lock:
            expired = tuple(
                pending.token
                for pending in self._pending_by_token.values()
                if pending.expires_at <= now_ms
            )
            expired_suppressions = tuple(
                rpc_key
                for rpc_key, suppressed in self._suppressed_by_rpc.items()
                if suppressed.expires_at <= now_ms
            )
            expired_suppression_tokens = tuple(
                self._suppressed_by_rpc[rpc_key].token
                for rpc_key in expired_suppressions
                if not self._suppressed_by_rpc[rpc_key].notified
            )
            for rpc_key in expired_suppressions:
                self._suppressed_by_rpc.pop(rpc_key, None)
        for token in expired:
            self._resolve_token(token)
        for token in expired_suppression_tokens:
            self._notify_resolved(token)

    def _clear_connection(self, generation: int) -> None:
        with self._lock:
            if generation != self._primary_connection_generation:
                return
            unresolved = tuple(self._pending_by_token) + tuple(
                suppressed.token
                for suppressed in self._suppressed_by_rpc.values()
                if not suppressed.notified
            )
            self._pending_by_token.clear()
            self._pending_by_rpc.clear()
            self._suppressed_by_rpc.clear()
            self._frontend = None
            self._upstream = None
            self._events = None
            self._decisions = None
            self._connection_stop = None
            self._primary_connection_generation = 0
        for token in unresolved:
            self._notify_resolved(token)

    def _notify_resolved(self, token: str) -> None:
        callback = self._on_approval_resolved
        if callback is None:
            return
        try:
            callback(token)
        except Exception:
            pass

    def _new_token_locked(self, rpc_key: tuple[str, str]) -> str:
        sequence = self._next_token
        self._next_token += 1
        digest = hashlib.sha256(
            (
                f"{self._connection_generation}\0{sequence}\0"
                f"{rpc_key[0]}\0{rpc_key[1]}"
            ).encode("utf-8")
        ).hexdigest()
        return f"codex-tui:{digest}"


def _approval_result(
    pending: _PendingApproval,
    decision: str,
) -> dict[str, Any]:
    if pending.method != _PERMISSIONS_APPROVAL_METHOD:
        return {"decision": decision}
    permissions = (
        deepcopy(pending.requested_permissions)
        if decision == "accept" and pending.requested_permissions is not None
        else {}
    )
    return {"permissions": permissions, "scope": "turn"}


class _ServerWebSocketConnection:
    def __init__(
        self,
        connection: socket.socket,
        *,
        max_message_bytes: int,
        handshake_timeout_seconds: float,
    ) -> None:
        self._socket: socket.socket | None = connection
        self._buffer = bytearray()
        self.max_message_bytes = max_message_bytes
        self.handshake_timeout_seconds = handshake_timeout_seconds

    def accept_handshake(self) -> None:
        connection = self._require_socket()
        connection.settimeout(self.handshake_timeout_seconds)
        header_bytes = self._read_headers()
        try:
            lines = header_bytes.decode("ascii").split("\r\n")
        except UnicodeDecodeError as error:
            raise AppServerTransportError("TUI handshake is not ASCII") from error
        if not lines or not lines[0].startswith("GET "):
            raise AppServerTransportError("TUI handshake request is invalid")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            if ":" not in line:
                raise AppServerTransportError("TUI handshake header is malformed")
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        if headers.get("upgrade", "").lower() != "websocket":
            raise AppServerTransportError("TUI did not request WebSocket upgrade")
        if "upgrade" not in headers.get("connection", "").lower():
            raise AppServerTransportError("TUI WebSocket connection header is invalid")
        if headers.get("sec-websocket-version") != "13":
            raise AppServerTransportError("TUI WebSocket version is unsupported")
        key = headers.get("sec-websocket-key", "")
        try:
            decoded_key = base64.b64decode(key, validate=True)
        except ValueError as error:
            raise AppServerTransportError("TUI WebSocket key is invalid") from error
        if len(decoded_key) != 16:
            raise AppServerTransportError("TUI WebSocket key is invalid")
        accept = base64.b64encode(
            hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
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

    def send_message(self, message: dict[str, Any]) -> None:
        payload = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > self.max_message_bytes:
            raise AppServerTransportError("TUI proxy message exceeds frame limit")
        connection = self._require_socket()
        connection.settimeout(self.handshake_timeout_seconds)
        self._send_frame(0x1, payload)

    def receive_message(self, timeout_seconds: float) -> dict[str, Any] | None:
        if timeout_seconds < 0:
            raise ValueError("receive timeout must not be negative")
        connection = self._require_socket()
        deadline = time.monotonic() + timeout_seconds
        while True:
            connection.settimeout(max(0.0, deadline - time.monotonic()))
            first, second = self._read_exact(2)
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            if first & 0x70:
                raise AppServerTransportError("unsupported TUI WebSocket extension")
            if not fin:
                raise AppServerTransportError("fragmented TUI frames are unsupported")
            if not second & 0x80:
                raise AppServerTransportError("TUI WebSocket frame must be masked")
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            if length > self.max_message_bytes:
                raise AppServerTransportError("TUI WebSocket frame exceeds limit")
            mask = self._read_exact(4)
            payload = self._read_exact(length)
            decoded = bytes(
                value ^ mask[index % 4]
                for index, value in enumerate(payload)
            )
            if opcode == 0x8:
                raise ConnectionError("TUI closed the WebSocket connection")
            if opcode == 0x9:
                self._send_frame(0xA, decoded)
                if time.monotonic() >= deadline:
                    return None
                continue
            if opcode == 0xA:
                if time.monotonic() >= deadline:
                    return None
                continue
            if opcode != 0x1:
                raise AppServerTransportError("TUI frame must contain text")
            try:
                value = json.loads(decoded.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AppServerTransportError("TUI emitted invalid JSON") from error
            if not isinstance(value, dict):
                raise AppServerTransportError("TUI message must be an object")
            return value

    def has_input(self) -> bool:
        if self._buffer:
            return True
        connection = self._require_socket()
        readable, _, _ = select.select((connection,), (), (), 0)
        return bool(readable)

    def close(self) -> None:
        connection = self._socket
        self._socket = None
        self._buffer.clear()
        if connection is None:
            return
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()

    def _read_headers(self) -> bytes:
        connection = self._require_socket()
        marker = b"\r\n\r\n"
        value = bytearray()
        while marker not in value:
            chunk = connection.recv(4096)
            if not chunk:
                raise ConnectionError("TUI closed during WebSocket handshake")
            value.extend(chunk)
            if len(value) > MAX_HTTP_HEADER_BYTES:
                raise AppServerTransportError("TUI handshake headers are oversized")
        header, remainder = value.split(marker, 1)
        self._buffer.extend(remainder)
        return bytes(header)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        connection = self._require_socket()
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, length))
        elif length <= 0xFFFF:
            header = bytes((0x80 | opcode, 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 127)) + struct.pack("!Q", length)
        connection.sendall(header + payload)

    def _read_exact(self, count: int) -> bytes:
        connection = self._require_socket()
        while len(self._buffer) < count:
            chunk = connection.recv(max(4096, count - len(self._buffer)))
            if not chunk:
                raise ConnectionError("TUI WebSocket connection closed")
            self._buffer.extend(chunk)
        value = bytes(self._buffer[:count])
        del self._buffer[:count]
        return value

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise RuntimeError("TUI WebSocket connection is closed")
        return self._socket


def _rpc_id(value: object) -> str | int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("request id must be a string or integer")
    if isinstance(value, str) and (not value or len(value) > 512):
        raise ValueError("request id is invalid")
    return value


def _rpc_key(value: str | int) -> tuple[str, str]:
    return ("string" if isinstance(value, str) else "integer", str(value))
