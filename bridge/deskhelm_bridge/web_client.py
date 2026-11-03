from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import secrets
import select
import socket
import sys
import threading
from threading import Event, Lock, Thread
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit
import uuid

from .adapter import AdapterCapability
from .client import get_session_catalog, get_session_snapshot, send_control_command
from .control import (
    ApprovalDecisionPayload,
    ConfigureSessionPayload,
    ControlCommand,
    ControlKind,
    FocusPayload,
    InterruptPayload,
    SubmitPromptPayload,
)
from .event import PROTOCOL_VERSION, AgentEvent
from .paths import deskhelm_runtime_directory
from .transport import (
    INTERACTION_SUBSCRIPTION_V1_CAPABILITY,
    MAX_FRAME_BYTES,
    PROTOCOL_ERROR_MESSAGE_TYPE,
    STATE_SUBSCRIPTION_V1_CAPABILITY,
    ClientHello,
    ClientRole,
    ProtocolError,
    decode_json_object,
    encode_frame,
)


MAX_PROMPT_CHARS = 64 * 1024
MAX_CONTROL_BODY_BYTES = 256 * 1024
MAX_EVENT_BYTES = 48_000
MAX_EVENTS_PER_SESSION = 120
MAX_EVENTS = 480
MAX_SSE_CLIENTS = 8
MAX_IDENTITY_CHARS = 512
WEB_COOKIE_NAME = "deskhelm_web"
WEB_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,256}")
_UNAUTHORIZED_BODY = (
    "请打开 deskhelm web 启动时打印的链接。\n"
    "Open the link that deskhelm web printed when it started.\n"
).encode("utf-8")
_SESSION_SEPARATOR = "\u001f"
_ASSET_DIR = Path(__file__).resolve().parent / "web-assets"
_ASSET_SPECS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
_CSP = (
    "default-src 'self'; style-src 'self'; script-src 'self'; "
    "img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'"
)
_CONTROL_KINDS = {
    ControlKind.FOCUS,
    ControlKind.INTERRUPT,
    ControlKind.SUBMIT_PROMPT,
    ControlKind.APPROVE,
    ControlKind.REJECT,
    ControlKind.CONFIGURE_SESSION,
}


class WebControlError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class _FrameBuffer:
    """Unbuffered NDJSON split for one subscription socket.

    select() reports only bytes still in the kernel. A makefile reader can
    take every line from one recv and leave the rest invisible until the
    socket becomes readable again.
    """

    def __init__(self) -> None:
        self._pending = bytearray()

    def push(self, data: bytes) -> None:
        if not data:
            raise ConnectionError("bridge subscription closed")
        self._pending.extend(data)
        if b"\n" not in self._pending and len(self._pending) > MAX_FRAME_BYTES:
            raise ProtocolError(
                f"frame exceeds maximum size of {MAX_FRAME_BYTES} bytes"
            )

    def pop(self) -> bytes | None:
        newline = self._pending.find(b"\n")
        if newline < 0:
            return None
        raw = bytes(self._pending[:newline])
        del self._pending[: newline + 1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if not raw:
            raise ProtocolError("frame must not be empty")
        if len(raw) > MAX_FRAME_BYTES:
            raise ProtocolError(
                f"frame exceeds maximum size of {MAX_FRAME_BYTES} bytes"
            )
        return raw


def session_key(agent_id: object, session_id: object, project_id: object) -> str:
    if (
        not isinstance(agent_id, str)
        or not isinstance(session_id, str)
        or not isinstance(project_id, str)
    ):
        raise ValueError("session identity is incomplete")
    parts = (agent_id, session_id, project_id)
    if any(not part.strip() for part in parts):
        raise ValueError("session identity is incomplete")
    if any(len(part) > MAX_IDENTITY_CHARS or _SESSION_SEPARATOR in part for part in parts):
        raise ValueError("session identity is incomplete")
    return _SESSION_SEPARATOR.join(parts)


def _config_setting(value: dict, name: str, limit: int) -> tuple[bool, str]:
    if name not in value:
        return False, ""
    field = value[name]
    if field is None:
        return True, ""
    if not isinstance(field, str):
        raise WebControlError(400, "invalid_control_request")
    text = field.strip()
    if (
        not text
        or len(text) > limit
        or any(character.isspace() or ord(character) < 32 for character in text)
    ):
        raise WebControlError(400, "invalid_control_request")
    return True, text


def _catalog_identity(path: str) -> tuple[str, str, str]:
    query = parse_qs(urlsplit(path).query, keep_blank_values=False)

    def one(name: str) -> str:
        values = query.get(name, [])
        if len(values) != 1 or not isinstance(values[0], str):
            raise WebControlError(400, "invalid_catalog_request")
        return values[0]

    try:
        agent_id = one("agent_id")
        session_id = one("session_id")
        project_id = one("project_id")
        session_key(agent_id, session_id, project_id)
    except WebControlError:
        raise
    except ValueError as error:
        raise WebControlError(400, "invalid_catalog_request") from error
    for part in (agent_id, session_id, project_id):
        if any(character.isspace() or ord(character) < 32 for character in part):
            raise WebControlError(400, "invalid_catalog_request")
    return agent_id, session_id, project_id


def normalize_web_host(host: str, *, allow_remote: bool) -> str:
    if not isinstance(host, str):
        raise ValueError("web host must be a string")
    value = host.strip()
    if value == "localhost":
        value = "127.0.0.1"
    if value == "127.0.0.1":
        return value
    if not allow_remote:
        raise ValueError("web client binds to 127.0.0.1 unless --allow-remote is set")
    if not value or any(character.isspace() for character in value):
        raise ValueError("web host is invalid")
    return value


def normalize_web_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("web port must be between 0 and 65535")
    return port


def default_web_token_path() -> Path:
    return deskhelm_runtime_directory() / "web-token"


def load_web_token(path: Path) -> str:
    """Return the token stored at path, creating one with mode 0600 if needed.

    Keeping the token across restarts keeps existing browser cookies valid.
    """
    try:
        token = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        token = ""
    if _TOKEN_PATTERN.fullmatch(token):
        return token
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(token + "\n")
    os.replace(temporary, path)
    return token


def _cookie_value(header: str, name: str) -> str:
    for part in header.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            return value.strip()
    return ""


def _same_origin(origin: str, host: str | None) -> bool:
    parts = urlsplit(origin)
    return parts.scheme == "http" and bool(host) and parts.netloc == host


def _load_assets() -> dict[str, tuple[bytes, str]]:
    assets: dict[str, tuple[bytes, str]] = {}
    for path, (name, content_type) in _ASSET_SPECS.items():
        assets[path] = ((_ASSET_DIR / name).read_bytes(), content_type)
    return assets


_ASSETS = _load_assets()


def _sse_json(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _public_token(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > 128 or any(ord(character) < 32 for character in text):
        return ""
    return text


def _public_capabilities(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    known = {item.value for item in AdapterCapability}
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item in known and item not in names:
            names.append(item)
        if len(names) >= len(known):
            break
    return names


class WebClient:
    """Loopback Codex-style viewer for one Bridge socket.

    The process holds one state subscription and one interaction subscription,
    then fans those frames out to browsers. Browsers never speak the Unix
    protocol directly.
    """

    def __init__(
        self,
        socket_path: Path,
        *,
        host: str,
        port: int,
        client_id: str,
        session_refresh_seconds: float = 1.0,
        access_token: str | None = None,
    ) -> None:
        if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 128:
            raise ValueError("web client_id is invalid")
        if session_refresh_seconds <= 0:
            raise ValueError("session refresh interval must be greater than zero")
        token = secrets.token_urlsafe(32) if access_token is None else access_token
        if not isinstance(token, str) or not _TOKEN_PATTERN.fullmatch(token):
            raise ValueError("web access token is invalid")
        self.access_token = token
        self.socket_path = socket_path
        self.host = host
        self.port = port
        self.client_id = client_id
        self.session_refresh_seconds = session_refresh_seconds
        self._epoch = uuid.uuid4().hex
        self._lock = Lock()
        self._stop = Event()
        self._connected = False
        self._had_subscription = False
        self._notice = ""
        self._sessions: list[dict[str, Any]] = []
        self._state_events: dict[int, AgentEvent] = {}
        self._focused: tuple[str, str, str] | None = None
        self._events: list[dict[str, Any]] = []
        self._approvals: dict[str, dict[str, Any]] = {}
        self._attention: dict[str, bool] = {}
        self._clients: list[queue.Queue[tuple[str, str] | None]] = []
        self._live_sockets: list[socket.socket] = []
        self._thread: Thread | None = None
        self._http_thread: Thread | None = None
        self._serving = Event()
        self._httpd: ThreadingHTTPServer | None = None

    def start(self) -> None:
        if self._httpd is not None:
            raise RuntimeError("web client is already started")
        self._stop.clear()
        self._thread = Thread(
            target=self._bridge_loop,
            name="deskhelm-web-bridge",
            daemon=True,
        )
        self._thread.start()
        try:
            self._httpd = ThreadingHTTPServer(
                (self.host, self.port),
                self._handler_class(),
            )
        except OSError:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=2)
            raise
        self._httpd.daemon_threads = True
        bound_host, bound_port = self._httpd.server_address[:2]
        self.host = str(bound_host)
        self.port = int(bound_port)
        self._http_thread = Thread(
            target=self._serve_http,
            name="deskhelm-web-http",
            daemon=True,
        )
        self._http_thread.start()
        if not self._serving.wait(2):
            self.close()
            raise RuntimeError("web client did not start")

    def _serve_http(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        # Poll instead of serve_forever(). shutdown() deadlocks unless that
        # loop is already running, and close() must stay callable from the
        # thread that owns the process.
        httpd.timeout = 0.1
        self._serving.set()
        try:
            while not self._stop.is_set():
                httpd.handle_request()
        finally:
            self._serving.clear()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            live_sockets = list(self._live_sockets)
        for live_socket in live_sockets:
            try:
                live_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        httpd = self._httpd
        if httpd is not None:
            httpd.server_close()
            self._httpd = None
        http_thread = self._http_thread
        self._http_thread = None
        if http_thread is not None and http_thread is not threading.current_thread():
            http_thread.join(timeout=2)
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def session_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._session_payload_locked()

    def full_snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload = self._session_payload_locked()
            payload["events"] = [dict(event) for event in self._events]
        return payload

    def build_control(self, value: object, *, now_ms: int | None = None) -> ControlCommand:
        if not isinstance(value, dict):
            raise WebControlError(400, "invalid_control_request")
        try:
            kind = ControlKind(value.get("kind"))
        except ValueError as error:
            raise WebControlError(400, "invalid_control_request") from error
        if kind not in _CONTROL_KINDS:
            raise WebControlError(400, "invalid_control_request")
        try:
            key = session_key(
                value.get("agent_id"),
                value.get("session_id"),
                value.get("project_id"),
            )
        except ValueError as error:
            raise WebControlError(400, "invalid_control_request") from error
        agent_id, session_id, project_id = key.split(_SESSION_SEPARATOR)
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if kind is ControlKind.FOCUS:
            payload: Any = FocusPayload()
            expires_at = now + 10_000
        elif kind is ControlKind.INTERRUPT:
            payload = InterruptPayload("web")
            expires_at = now + 10_000
        elif kind is ControlKind.SUBMIT_PROMPT:
            text = value.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_PROMPT_CHARS:
                raise WebControlError(400, "invalid_control_request")
            payload = SubmitPromptPayload(text)
            expires_at = now + 10_000
        elif kind is ControlKind.CONFIGURE_SESSION:
            model_set, model = _config_setting(value, "model", 128)
            effort_set, effort = _config_setting(value, "reasoning_effort", 64)
            try:
                payload = ConfigureSessionPayload(
                    model_set=model_set,
                    model=model,
                    effort_set=effort_set,
                    reasoning_effort=effort,
                )
            except ValueError as error:
                raise WebControlError(400, "invalid_control_request") from error
            expires_at = now + 10_000
        else:
            request_id = value.get("request_id")
            if not isinstance(request_id, str) or not request_id.strip():
                raise WebControlError(400, "invalid_control_request")
            with self._lock:
                approval = dict(self._approvals[key]) if key in self._approvals else None
            if approval is None or approval["request_id"] != request_id:
                raise WebControlError(409, "approval_not_found")
            if approval["expires_at"] <= now:
                with self._lock:
                    current = self._approvals.get(key)
                    if current is not None and current["request_id"] == request_id:
                        self._approvals.pop(key, None)
                raise WebControlError(409, "expired")
            payload = ApprovalDecisionPayload(
                request_id=approval["request_id"],
                summary=approval["summary"],
                request_expires_at=approval["expires_at"],
            )
            expires_at = approval["expires_at"]
        return ControlCommand(
            kind=kind,
            agent_id=agent_id,
            session_id=session_id,
            project_id=project_id,
            issued_by=self.client_id,
            issued_at=now,
            expires_at=expires_at,
            idempotency_key=f"web:{kind.value}:{uuid.uuid4()}",
            payload=payload,
        )

    def dispatch(self, value: object) -> tuple[int, dict[str, Any]]:
        try:
            command = self.build_control(value)
        except WebControlError as error:
            return error.status, {"error": error.code}
        try:
            result = send_control_command(
                command,
                self.socket_path,
                client_id=self.client_id,
            )
        except (ConnectionError, OSError, ValueError):
            return 503, {"error": "bridge_unavailable"}
        if (
            result.code.value == "dispatched"
            and command.kind in {ControlKind.APPROVE, ControlKind.REJECT}
            and isinstance(command.payload, ApprovalDecisionPayload)
        ):
            key = session_key(command.agent_id, command.session_id, command.project_id)
            with self._lock:
                approval = self._approvals.get(key)
                if approval is not None and approval["request_id"] == command.payload.request_id:
                    self._approvals.pop(key, None)
        self.refresh_sessions()
        return 200, {
            "status": result.status.value,
            "code": result.code.value,
            "duplicate": result.duplicate,
        }

    def refresh_sessions(self) -> None:
        try:
            result = get_session_snapshot(
                self.socket_path,
                client_id=f"{self.client_id}-query",
            )
        except (ConnectionError, OSError, ValueError):
            with self._lock:
                self._notice = "bridge unavailable"
            self._broadcast("sessions", self.session_snapshot())
            return
        sessions = result.get("sessions")
        focused = result.get("focused_session")
        focused_key = None
        if isinstance(focused, dict):
            try:
                focused_key = tuple(
                    session_key(
                        focused.get("agent_id"),
                        focused.get("session_id"),
                        focused.get("project_id"),
                    ).split(_SESSION_SEPARATOR)
                )
            except ValueError:
                focused_key = None
        with self._lock:
            if isinstance(sessions, list):
                self._sessions = [
                    dict(item) for item in sessions if isinstance(item, dict)
                ]
            self._focused = focused_key  # type: ignore[assignment]
            if self._connected and self._notice == "bridge unavailable":
                self._notice = ""
        self._broadcast("sessions", self.session_snapshot())

    def remember_interaction(self, event: dict[str, Any]) -> dict[str, Any] | None:
        try:
            key = session_key(
                event.get("agent_id"),
                event.get("session_id"),
                event.get("project_id"),
            )
        except ValueError:
            return None
        try:
            encoded = json.dumps(event, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            return None
        if len(encoded) > MAX_EVENT_BYTES:
            with self._lock:
                self._notice = "interaction event exceeded the web client bound"
            self._broadcast("sessions", self.session_snapshot())
            return None
        public = dict(event)
        public["key"] = key
        with self._lock:
            self._events.append(public)
            self._apply_event_locked(key, public)
            self._trim_locked()
        return public

    def handle_get(self, request: BaseHTTPRequestHandler) -> None:
        path = urlsplit(request.path).path
        if path == "/api/events":
            self._stream_events(request)
            return
        if path == "/api/snapshot":
            self._send_json(request, 200, self.full_snapshot())
            return
        if path == "/api/catalog":
            self._send_catalog(request)
            return
        asset = _ASSETS.get(path)
        if asset is None:
            self._send_json(request, 404, {"error": "not_found"})
            return
        body, content_type = asset
        self._send_bytes(request, 200, body, content_type)

    def handle_post(self, request: BaseHTTPRequestHandler) -> None:
        path = urlsplit(request.path).path
        if path != "/api/control":
            self._refuse(request, 404, {"error": "not_found"})
            return
        media_type = request.headers.get("Content-Type", "").split(";", 1)[0]
        if media_type.strip().lower() != "application/json":
            self._refuse(request, 415, {"error": "json_required"})
            return
        origin = request.headers.get("Origin")
        if origin is not None and not _same_origin(origin, request.headers.get("Host")):
            self._refuse(request, 403, {"error": "origin_not_allowed"})
            return
        try:
            length = int(request.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_CONTROL_BODY_BYTES:
            self._send_json(request, 400, {"error": "invalid_control_request"})
            return
        raw = request.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._send_json(request, 400, {"error": "invalid_control_request"})
            return
        status, payload = self.dispatch(value)
        self._send_json(request, status, payload)

    def _send_catalog(self, request: BaseHTTPRequestHandler) -> None:
        try:
            agent_id, session_id, project_id = _catalog_identity(request.path)
        except WebControlError as error:
            self._send_json(request, error.status, {"error": error.code})
            return
        try:
            result = get_session_catalog(
                self.socket_path,
                client_id=f"{self.client_id}-catalog",
                agent_id=agent_id,
                session_id=session_id,
                project_id=project_id,
            )
        except (ConnectionError, OSError, TimeoutError, ValueError):
            self._send_json(request, 503, {"error": "bridge_unavailable"})
            return
        public = {
            "agent_id": agent_id,
            "session_id": session_id,
            "project_id": project_id,
        }
        for field_name in (
            "available",
            "notice",
            "model",
            "reasoning_effort",
            "models",
            "models_notice",
            "mcp_servers",
            "mcp_notice",
            "skills",
            "skills_notice",
            "truncated",
        ):
            if field_name in result:
                public[field_name] = result[field_name]
        self._send_json(request, 200, public)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        client = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return None

            def do_GET(self) -> None:  # noqa: N802
                if client.admit(self):
                    client.handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                if client.admit(self):
                    client.handle_post(self)

        return Handler

    def admit(self, request: BaseHTTPRequestHandler) -> bool:
        """Check the Host and the credential; a refused request is answered."""
        if not self._host_allowed(request.headers.get("Host")):
            self._refuse(request, 403, {"error": "host_not_allowed"})
            return False
        split = urlsplit(request.path)
        offered = parse_qs(split.query).get("token")
        if offered and request.command == "GET":
            if self._token_matches(offered[0]):
                self._set_cookie_and_redirect(request, split.path)
            else:
                self._send_unauthorized(request)
            return False
        if not self._token_matches(self._credential(request)):
            self._send_unauthorized(request)
            return False
        return True

    def _host_allowed(self, host: str | None) -> bool:
        if self.host != "127.0.0.1":
            # --allow-remote binds beyond loopback, where any name may reach
            # the server; the token still guards every request.
            return True
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if self.port == 80:
            allowed |= {"127.0.0.1", "localhost"}
        return host in allowed

    def _credential(self, request: BaseHTTPRequestHandler) -> str:
        scheme, _, value = request.headers.get("Authorization", "").partition(" ")
        if scheme == "Bearer" and value.strip():
            return value.strip()
        return _cookie_value(request.headers.get("Cookie", ""), WEB_COOKIE_NAME)

    def _token_matches(self, value: str) -> bool:
        return bool(value) and hmac.compare_digest(
            value.encode("utf-8"), self.access_token.encode("utf-8")
        )

    def _set_cookie_and_redirect(
        self, request: BaseHTTPRequestHandler, path: str
    ) -> None:
        # Redirect drops the token from the address bar; "//name" would leave
        # this origin.
        location = path if path.startswith("/") and not path.startswith("//") else "/"
        request.close_connection = True
        request.send_response(303)
        request.send_header("Location", location)
        request.send_header(
            "Set-Cookie",
            f"{WEB_COOKIE_NAME}={self.access_token}; Path=/; HttpOnly; "
            f"SameSite=Strict; Max-Age={WEB_COOKIE_MAX_AGE_SECONDS}",
        )
        request.send_header("Content-Length", "0")
        request.send_header("Cache-Control", "no-store")
        request.send_header("Referrer-Policy", "no-referrer")
        request.send_header("Connection", "close")
        request.end_headers()

    def _send_unauthorized(self, request: BaseHTTPRequestHandler) -> None:
        request.close_connection = True
        self._send_bytes(
            request,
            401,
            _UNAUTHORIZED_BODY,
            "text/plain; charset=utf-8",
            extra_headers=(
                ("WWW-Authenticate", 'Bearer realm="deskhelm-web"'),
                ("Connection", "close"),
            ),
        )

    def _refuse(
        self,
        request: BaseHTTPRequestHandler,
        status: int,
        payload: dict[str, Any],
    ) -> None:
        # The request body may be unread, so the connection cannot be reused.
        request.close_connection = True
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send_bytes(
            request,
            status,
            body,
            "application/json; charset=utf-8",
            extra_headers=(("Connection", "close"),),
        )

    def _send_json(
        self,
        request: BaseHTTPRequestHandler,
        status: int,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(request, status, body, "application/json; charset=utf-8")

    def _send_bytes(
        self,
        request: BaseHTTPRequestHandler,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        request.send_response(status)
        request.send_header("Content-Type", content_type)
        request.send_header("Content-Length", str(len(body)))
        request.send_header("Cache-Control", "no-store")
        request.send_header("X-Content-Type-Options", "nosniff")
        request.send_header("X-Robots-Tag", "noindex, nofollow")
        request.send_header("Referrer-Policy", "no-referrer")
        request.send_header("Content-Security-Policy", _CSP)
        for name, value in extra_headers:
            request.send_header(name, value)
        request.end_headers()
        request.wfile.write(body)

    def _stream_events(self, request: BaseHTTPRequestHandler) -> None:
        try:
            events = self._subscribe()
        except WebControlError as error:
            self._send_json(request, error.status, {"error": error.code})
            return
        request.send_response(200)
        request.send_header("Content-Type", "text/event-stream; charset=utf-8")
        request.send_header("Cache-Control", "no-store")
        request.send_header("Connection", "close")
        request.send_header("X-Content-Type-Options", "nosniff")
        request.send_header("X-Robots-Tag", "noindex, nofollow")
        request.send_header("Referrer-Policy", "no-referrer")
        request.send_header("Content-Security-Policy", _CSP)
        request.end_headers()
        request.connection.settimeout(5)
        try:
            self._write_sse(request, "snapshot", _sse_json(self.full_snapshot()))
            idle = 0
            while not self._stop.is_set():
                try:
                    item = events.get(timeout=1)
                except queue.Empty:
                    idle += 1
                    if idle >= 15:
                        request.wfile.write(b": keepalive\n\n")
                        request.wfile.flush()
                        idle = 0
                    continue
                if item is None:
                    break
                name, data = item
                self._write_sse(request, name, data)
                idle = 0
        except (BrokenPipeError, ConnectionError, TimeoutError, OSError):
            return
        finally:
            self._unsubscribe(events)

    def _write_sse(self, request: BaseHTTPRequestHandler, name: str, data: str) -> None:
        if "\n" in data or "\r" in data:
            raise ValueError("sse data must be one line")
        request.wfile.write(f"event: {name}\ndata: {data}\n\n".encode())
        request.wfile.flush()

    def _subscribe(self) -> queue.Queue[tuple[str, str] | None]:
        subscriber: queue.Queue[tuple[str, str] | None] = queue.Queue(maxsize=32)
        with self._lock:
            if len(self._clients) >= MAX_SSE_CLIENTS:
                raise WebControlError(503, "too_many_clients")
            self._clients.append(subscriber)
        return subscriber

    def _unsubscribe(self, subscriber: queue.Queue[tuple[str, str] | None]) -> None:
        with self._lock:
            if subscriber in self._clients:
                self._clients.remove(subscriber)

    def _broadcast(self, name: str, payload: dict[str, Any]) -> None:
        data = _sse_json(payload)
        with self._lock:
            clients = list(self._clients)
        for subscriber in clients:
            try:
                subscriber.put_nowait((name, data))
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(None)
                except queue.Full:
                    pass
                self._unsubscribe(subscriber)

    def _session_payload_locked(self) -> dict[str, Any]:
        now = int(time.time() * 1000)
        sessions: list[dict[str, Any]] = []
        for record in self._sessions:
            try:
                key = session_key(
                    record.get("agent_id"),
                    record.get("session_id"),
                    record.get("project_id"),
                )
            except ValueError:
                continue
            slot = record.get("slot")
            event = (
                self._state_events.get(slot)
                if isinstance(slot, int) and not isinstance(slot, bool)
                else None
            )
            # Bridge fills every slot with a synthetic offline event named
            # slot-N. That placeholder is not this session's agent state.
            if event is not None and event.agent_id != record.get("agent_id"):
                event = None
            approval = self._approvals.get(key)
            if approval is not None and approval["expires_at"] <= now:
                self._approvals.pop(key, None)
                approval = None
            agent_id, session_id, project_id = key.split(_SESSION_SEPARATOR)
            record_updated = record.get("updated_at")
            updated_at = record_updated if isinstance(record_updated, int) else 0
            if event is not None:
                updated_at = max(updated_at, event.updated_at)
            registered = record.get("registered_at")
            lifecycle = record.get("state")
            sessions.append(
                {
                    "key": key,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "project_id": project_id,
                    "slot": slot if isinstance(slot, int) and not isinstance(slot, bool) else None,
                    "lifecycle": lifecycle if isinstance(lifecycle, str) else "",
                    "label": event.label if event is not None else "",
                    "agent_state": event.state.value if event is not None else "",
                    "progress": event.progress if event is not None else None,
                    "updated_at": updated_at,
                    "registered_at": registered if isinstance(registered, int) else 0,
                    "attention": bool(self._attention.get(key, False)),
                    "adapter_id": _public_token(record.get("adapter_id")),
                    "runtime_name": _public_token(record.get("runtime_name")),
                    "runtime_version": _public_token(record.get("runtime_version")),
                    "capabilities": _public_capabilities(record.get("capabilities")),
                    "approval": None
                    if approval is None
                    else {
                        "request_id": approval["request_id"],
                        "summary": approval["summary"],
                        "expires_at": approval["expires_at"],
                    },
                }
            )
        focused = None
        if self._focused is not None:
            focused = {
                "agent_id": self._focused[0],
                "session_id": self._focused[1],
                "project_id": self._focused[2],
            }
        return {
            "epoch": self._epoch,
            "connected": self._connected,
            "notice": self._notice,
            "focused_session": focused,
            "sessions": sessions,
        }

    def _apply_event_locked(self, key: str, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if kind == "approval_request":
            request_id = payload.get("request_id")
            summary = payload.get("summary")
            expires_at = payload.get("expires_at")
            if (
                isinstance(request_id, str)
                and request_id.strip()
                and isinstance(summary, str)
                and summary
                and isinstance(expires_at, int)
                and not isinstance(expires_at, bool)
                and expires_at > 0
            ):
                self._approvals[key] = {
                    "request_id": request_id,
                    "summary": summary,
                    "expires_at": expires_at,
                }
            return
        if kind == "attention":
            self._attention[key] = payload.get("active") is True
            return
        if kind in {"task_completed", "task_failed"}:
            self._approvals.pop(key, None)
            self._attention[key] = False

    def _trim_locked(self) -> None:
        counts: dict[str, int] = {}
        for event in reversed(self._events):
            key = str(event.get("key", ""))
            counts[key] = counts.get(key, 0) + 1
            event["_drop"] = counts[key] > MAX_EVENTS_PER_SESSION
        self._events = [event for event in self._events if not event.pop("_drop", False)]
        if len(self._events) > MAX_EVENTS:
            del self._events[: len(self._events) - MAX_EVENTS]

    def _bridge_loop(self) -> None:
        delay = 0.2
        while not self._stop.is_set():
            try:
                self._serve_bridge_once()
                delay = 0.2
            except Exception:
                with self._lock:
                    self._connected = False
                    self._notice = "bridge unavailable"
                self._broadcast("sessions", self.session_snapshot())
                if self._stop.wait(delay):
                    return
                delay = min(2.0, delay * 2)

    def _serve_bridge_once(self) -> None:
        state_socket, state_pending = self._connect_subscriber(
            STATE_SUBSCRIPTION_V1_CAPABILITY,
            "state",
        )
        try:
            interaction_socket, interaction_pending = self._connect_subscriber(
                INTERACTION_SUBSCRIPTION_V1_CAPABILITY,
                "interaction",
            )
        except BaseException:
            state_socket.close()
            raise
        with self._lock:
            self._live_sockets = [state_socket, interaction_socket]
        try:
            with self._lock:
                restarted = self._had_subscription
                self._had_subscription = True
                self._connected = True
                if restarted:
                    self._notice = "interaction stream restarted"
                elif self._notice == "bridge unavailable":
                    self._notice = ""
            self.refresh_sessions()
            next_refresh = time.monotonic() + self.session_refresh_seconds
            while not self._stop.is_set():
                self._drain(state_pending, self._consume_state)
                self._drain(interaction_pending, self._consume_interaction)
                timeout = min(0.2, max(0.0, next_refresh - time.monotonic()))
                readable, _, _ = select.select(
                    [state_socket, interaction_socket],
                    [],
                    [],
                    timeout,
                )
                if state_socket in readable:
                    state_pending.push(state_socket.recv(65536))
                if interaction_socket in readable:
                    interaction_pending.push(interaction_socket.recv(65536))
                if time.monotonic() >= next_refresh:
                    self.refresh_sessions()
                    next_refresh = time.monotonic() + self.session_refresh_seconds
        finally:
            with self._lock:
                self._live_sockets = [
                    live
                    for live in self._live_sockets
                    if live is not state_socket and live is not interaction_socket
                ]
                self._connected = False
            for live_socket in (interaction_socket, state_socket):
                try:
                    live_socket.close()
                except OSError:
                    pass

    def _connect_subscriber(
        self, capability: str, suffix: str
    ) -> tuple[socket.socket, _FrameBuffer]:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(self.socket_path))
            pending = _FrameBuffer()
            hello = ClientHello(
                client_id=f"{self.client_id}-{suffix}",
                role=ClientRole.SUBSCRIBER,
                supported_versions=(PROTOCOL_VERSION,),
                capabilities=(capability,),
            )
            client.sendall(encode_frame(hello.to_dict()))
            response = self._read_handshake_frame(client, pending)
            if response.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
                raise ConnectionError("bridge rejected web client")
            accepted = response.get("accepted_capabilities", ())
            if capability not in accepted:
                raise ConnectionError("bridge did not accept web subscription")
            return client, pending
        except BaseException:
            client.close()
            raise

    def _read_handshake_frame(
        self, client: socket.socket, pending: _FrameBuffer
    ) -> dict[str, Any]:
        deadline = time.monotonic() + 5
        while True:
            frame = pending.pop()
            if frame is not None:
                return decode_json_object(frame)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConnectionError("bridge subscription handshake timed out")
            readable, _, _ = select.select([client], [], [], remaining)
            if not readable:
                raise ConnectionError("bridge subscription handshake timed out")
            pending.push(client.recv(65536))

    def _drain(self, pending: _FrameBuffer, consume: Callable[[bytes], None]) -> None:
        while True:
            frame = pending.pop()
            if frame is None:
                return
            consume(frame)

    def _consume_state(self, frame: bytes) -> None:
        value = decode_json_object(frame)
        message_type = value.get("message_type")
        changed = False
        with self._lock:
            if message_type == "state_snapshot":
                events: dict[int, AgentEvent] = {}
                for event in value.get("events", []):
                    if isinstance(event, dict):
                        parsed = AgentEvent.from_dict(event)
                        events[parsed.slot] = parsed
                self._state_events = events
                changed = True
            elif message_type == "state_update" and isinstance(value.get("event"), dict):
                parsed = AgentEvent.from_dict(value["event"])
                self._state_events[parsed.slot] = parsed
                changed = True
        if changed:
            self._broadcast("sessions", self.session_snapshot())

    def _consume_interaction(self, frame: bytes) -> None:
        value = decode_json_object(frame)
        if value.get("message_type") != "interaction_update":
            return
        event = value.get("event")
        if not isinstance(event, dict):
            return
        stored = self.remember_interaction(event)
        if stored is not None:
            self._broadcast("interaction", stored)


def run_web_client(
    socket_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
    client_id: str = "deskhelm-web",
    allow_remote: bool = False,
    session_refresh_seconds: float = 1.0,
    token_path: Path | None = None,
) -> int:
    normalized_host = normalize_web_host(host, allow_remote=allow_remote)
    normalized_port = normalize_web_port(port)
    token = load_web_token(token_path or default_web_token_path())
    client = WebClient(
        socket_path,
        host=normalized_host,
        port=normalized_port,
        client_id=client_id,
        session_refresh_seconds=session_refresh_seconds,
        access_token=token,
    )
    client.start()
    print(f"deskhelm web reading {socket_path}", file=sys.stderr)
    print(
        f"deskhelm web listening on http://{client.host}:{client.port}",
        file=sys.stderr,
    )
    print(
        "open once to sign this browser in: "
        f"http://{client.host}:{client.port}/?token={token}",
        file=sys.stderr,
    )
    if allow_remote and client.host not in {"127.0.0.1", "localhost"}:
        print(
            "deskhelm web is bound beyond loopback and can show interaction content",
            file=sys.stderr,
        )
    http_thread = client._http_thread
    try:
        while http_thread is not None and http_thread.is_alive():
            http_thread.join(timeout=0.2)
    except KeyboardInterrupt:
        raise
    finally:
        client.close()
    return 0 if http_thread is None or not http_thread.is_alive() else 1
