"""Loopback HTTP plumbing for the web surface (ADR 0036, ADR 0043).

Every request needs the startup token, once as ``?token=`` and then as an
HttpOnly, SameSite=Strict cookie. A loopback bind accepts only loopback Host
names; POST needs a JSON body and, when the browser sends one, a same-origin
Origin.
"""

from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import secrets
import threading
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import parse_qs, urlsplit
import uuid

from deskhelm_protocol.paths import runtime_directory

MAX_CONTROL_BODY_BYTES = 256 * 1024
MAX_SSE_CLIENTS = 8
# Streaming replies send one small patch per delta; a browser that falls this
# far behind reconnects and starts again from a snapshot.
MAX_SSE_QUEUE_EVENTS = 512
WEB_COOKIE_NAME = "deskhelm_web"
WEB_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,256}")
_UNAUTHORIZED_BODY = (
    "请打开 deskhelm web 启动时打印的链接。\n"
    "Open the link that deskhelm web printed when it started.\n"
).encode("utf-8")
_CSP = (
    "default-src 'self'; style-src 'self'; script-src 'self'; "
    "img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'"
)
ASSET_DIRECTORY = Path(__file__).resolve().parent / "assets"
_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def normalize_web_host(host: str, *, allow_remote: bool) -> str:
    value = host.strip() if isinstance(host, str) else ""
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
    # The file the v1 web client used, so existing browser sign-ins still work.
    return runtime_directory() / "web-token"


def load_web_token(path: Path) -> str:
    """Return the token stored at path, creating one with mode 0600 if needed."""
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


def sse_json(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


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
    """The page and every stylesheet, module, and icon under assets/."""
    assets = {"/": ((ASSET_DIRECTORY / "index.html").read_bytes(), "text/html; charset=utf-8")}
    for path in sorted(ASSET_DIRECTORY.rglob("*")):
        content_type = _CONTENT_TYPES.get(path.suffix)
        if content_type and path.is_file():
            assets["/assets/" + path.relative_to(ASSET_DIRECTORY).as_posix()] = (path.read_bytes(), content_type)
    return assets


class WebHttp:
    """Serve the page, an SSE stream, and a JSON API behind the token.

    Subclasses provide ``snapshot()``, ``api_get()``, and ``api_post()``.
    """

    def __init__(self, *, host: str, port: int, access_token: str | None = None) -> None:
        token = secrets.token_urlsafe(32) if access_token is None else access_token
        if not isinstance(token, str) or not _TOKEN_PATTERN.fullmatch(token):
            raise ValueError("web access token is invalid")
        self.access_token = token
        self.host = host
        self.port = port
        self.stop_event = Event()
        self._assets = _load_assets()
        self._clients_lock = Lock()
        self._clients: list[queue.Queue[tuple[str, str] | None]] = []
        self._serving = Event()
        self._httpd: ThreadingHTTPServer | None = None
        self._http_thread: Thread | None = None

    # Hooks -------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        raise NotImplementedError

    def api_get(self, request: BaseHTTPRequestHandler, path: str) -> tuple[int, dict[str, Any]] | None:
        return None

    def api_post(self, path: str, value: object) -> tuple[int, dict[str, Any]] | None:
        return None

    # Lifecycle ---------------------------------------------------------

    def start_http(self) -> None:
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._handler_class())
        self._httpd.daemon_threads = True
        bound_host, bound_port = self._httpd.server_address[:2]
        self.host, self.port = str(bound_host), int(bound_port)
        self._http_thread = Thread(target=self._serve_http, name="deskhelm-web-http", daemon=True)
        self._http_thread.start()
        if not self._serving.wait(2):
            self.close_http()
            raise RuntimeError("web server did not start")

    def close_http(self) -> None:
        self.stop_event.set()
        with self._clients_lock:
            clients = list(self._clients)
        for subscriber in clients:
            try:
                subscriber.put_nowait(None)
            except queue.Full:
                pass
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.server_close()
        thread, self._http_thread = self._http_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    @property
    def serving(self) -> bool:
        return self._http_thread is not None and self._http_thread.is_alive()

    def _serve_http(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        # Poll instead of serve_forever(): shutdown() deadlocks unless that
        # loop already runs, and close must stay callable from any thread.
        httpd.timeout = 0.1
        self._serving.set()
        try:
            while not self.stop_event.is_set():
                httpd.handle_request()
        finally:
            self._serving.clear()

    # Fan-out -----------------------------------------------------------

    def broadcast(self, name: str, payload: dict[str, Any]) -> None:
        data = sse_json(payload)
        with self._clients_lock:
            clients = list(self._clients)
        for subscriber in clients:
            try:
                subscriber.put_nowait((name, data))
            except queue.Full:
                # A browser that cannot keep up reconnects and gets a snapshot.
                self._unsubscribe(subscriber)
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(None)
                except (queue.Empty, queue.Full):
                    pass

    # Requests ----------------------------------------------------------

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        web = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return None

            def do_GET(self) -> None:  # noqa: N802
                if web._admit(self):
                    web._get(self)

            def do_POST(self) -> None:  # noqa: N802
                if web._admit(self):
                    web._post(self)

        return Handler

    def _get(self, request: BaseHTTPRequestHandler) -> None:
        path = urlsplit(request.path).path
        if path == "/api/events":
            self._stream_events(request)
            return
        if path == "/api/snapshot":
            self.send_json(request, 200, self.snapshot())
            return
        answered = self.api_get(request, path) if path.startswith("/api/") else None
        if answered is not None:
            self.send_json(request, *answered)
            return
        asset = self._assets.get(path)
        if asset is None:
            self.send_json(request, 404, {"error": "not_found"})
            return
        self._send_bytes(request, 200, *asset)

    def _post(self, request: BaseHTTPRequestHandler) -> None:
        path = urlsplit(request.path).path
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
            self._refuse(request, 400, {"error": "invalid_control_request"})
            return
        try:
            value = json.loads(request.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self.send_json(request, 400, {"error": "invalid_control_request"})
            return
        answered = self.api_post(path, value)
        if answered is None:
            self.send_json(request, 404, {"error": "not_found"})
            return
        self.send_json(request, *answered)

    def _admit(self, request: BaseHTTPRequestHandler) -> bool:
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
        return bool(value) and hmac.compare_digest(value.encode("utf-8"), self.access_token.encode("utf-8"))

    def _set_cookie_and_redirect(self, request: BaseHTTPRequestHandler, path: str) -> None:
        # The redirect drops the token from the address bar; "//name" would
        # leave this origin.
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
            (("WWW-Authenticate", 'Bearer realm="deskhelm-web"'), ("Connection", "close")),
        )

    def _refuse(self, request: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
        # The request body may be unread, so the connection cannot be reused.
        request.close_connection = True
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send_bytes(request, status, body, "application/json; charset=utf-8", (("Connection", "close"),))

    def send_json(self, request: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
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
        self._common_headers(request)
        for name, value in extra_headers:
            request.send_header(name, value)
        request.end_headers()
        try:
            request.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # A browser that left or reloaded mid-response is not an error.
            request.close_connection = True

    @staticmethod
    def _common_headers(request: BaseHTTPRequestHandler) -> None:
        request.send_header("Cache-Control", "no-store")
        request.send_header("X-Content-Type-Options", "nosniff")
        request.send_header("X-Robots-Tag", "noindex, nofollow")
        request.send_header("Referrer-Policy", "no-referrer")
        request.send_header("Content-Security-Policy", _CSP)

    def _stream_events(self, request: BaseHTTPRequestHandler) -> None:
        subscriber: queue.Queue[tuple[str, str] | None] = queue.Queue(maxsize=MAX_SSE_QUEUE_EVENTS)
        with self._clients_lock:
            if len(self._clients) >= MAX_SSE_CLIENTS:
                admitted = False
            else:
                self._clients.append(subscriber)
                admitted = True
        if not admitted:
            self.send_json(request, 503, {"error": "too_many_clients"})
            return
        request.send_response(200)
        request.send_header("Content-Type", "text/event-stream; charset=utf-8")
        request.send_header("Connection", "close")
        self._common_headers(request)
        request.end_headers()
        request.connection.settimeout(5)
        try:
            self._write_sse(request, "snapshot", sse_json(self.snapshot()))
            idle = 0
            while not self.stop_event.is_set():
                try:
                    item = subscriber.get(timeout=1)
                except queue.Empty:
                    idle += 1
                    if idle >= 15:
                        request.wfile.write(b": keepalive\n\n")
                        request.wfile.flush()
                        idle = 0
                    continue
                if item is None:
                    break
                self._write_sse(request, *item)
                idle = 0
        except (BrokenPipeError, ConnectionError, TimeoutError, OSError):
            return
        finally:
            self._unsubscribe(subscriber)

    @staticmethod
    def _write_sse(request: BaseHTTPRequestHandler, name: str, data: str) -> None:
        if "\n" in data or "\r" in data:
            raise ValueError("sse data must be one line")
        request.wfile.write(f"event: {name}\ndata: {data}\n\n".encode())
        request.wfile.flush()

    def _unsubscribe(self, subscriber: queue.Queue[tuple[str, str] | None]) -> None:
        with self._clients_lock:
            if subscriber in self._clients:
                self._clients.remove(subscriber)
