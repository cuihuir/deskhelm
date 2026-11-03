"""The loopback web surface on protocol v2 (ADR 0036, ADR 0043).

One operator connection through deskhelm_client feeds every browser. The
Bridge's bounded interaction window arrives on connect, so a refreshed page
shows recent activity and a reconnecting page still sees pending approvals.
The page keeps the v1 HTTP API; approvals name the Bridge approval id.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
from threading import Lock
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit
import uuid

from deskhelm_client import BridgeClient, ConsoleChange, result_text
from deskhelm_protocol import SessionKey
from deskhelm_protocol.model import MAX_IDENTITY_CHARS, MAX_LABEL_CHARS, MAX_TEXT_CHARS

from .http import WebHttp

MAX_EVENTS_PER_SESSION = 120
MAX_EVENTS = 480
MAX_EVENT_BYTES = 48_000
_SEPARATOR = "\u001f"
_AGENT_LABELS = {"codex": "Codex", "claude": "Claude", "grok": "Grok"}
_CATALOG_FIELDS = (
    "model",
    "reasoning_effort",
    "models",
    "models_notice",
    "mcp_servers",
    "mcp_notice",
    "skills",
    "skills_notice",
    "truncated",
)


class WebSurface(WebHttp):
    def __init__(
        self,
        socket_path: Path,
        *,
        host: str,
        port: int,
        client_id: str = "deskhelm-web",
        access_token: str | None = None,
    ) -> None:
        super().__init__(host=host, port=port, access_token=access_token)
        self.socket_path = socket_path
        self._lock = Lock()
        self._epoch = uuid.uuid4().hex
        self._notice = ""
        self._events: list[dict[str, Any]] = []
        self._updated: dict[SessionKey, int] = {}
        self.client = BridgeClient(
            socket_path,
            client_id=client_id,
            interaction=True,
            on_console=self._on_console,
            on_interaction=self._on_interaction,
            on_connection=self._on_connection,
        )

    def start(self) -> None:
        self.client.start()
        self.start_http()

    def close(self) -> None:
        self.close_http()
        self.client.close()

    # Bridge to browsers ------------------------------------------------

    def _on_console(self, change: ConsoleChange) -> None:
        now = int(time.time() * 1000)
        with self._lock:
            if change.restarted:
                # A new Bridge holds none of the old activity.
                self._epoch = uuid.uuid4().hex
                self._events = []
                self._updated = {}
                self._notice = "interaction stream restarted"
            for key, view in change.state.sessions.items():
                if change.reset or change.previous.sessions.get(key) is not view:
                    self._updated[key] = max(self._updated.get(key, 0), int(view.get("opened_at") or 0), now)
            for key in [key for key in self._updated if key not in change.state.sessions]:
                del self._updated[key]
        self.broadcast("sessions", self.sessions_payload())

    def _on_interaction(self, event: dict[str, Any], replay: bool) -> None:
        stored = self._remember(event, replay)
        if stored is not None:
            self.broadcast("interaction", stored)

    def _on_connection(self, connected: bool) -> None:
        with self._lock:
            if not connected:
                self._notice = "bridge unavailable"
            elif self._notice == "bridge unavailable":
                self._notice = ""
        self.broadcast("sessions", self.sessions_payload())

    def _remember(self, event: dict[str, Any], replay: bool) -> dict[str, Any] | None:
        try:
            key = SessionKey.from_dict(event)
        except ValueError:
            return None
        public = {**event, "key": _joined(key)}
        if len(json.dumps(public, ensure_ascii=False).encode("utf-8")) > MAX_EVENT_BYTES:
            with self._lock:
                self._notice = "interaction event exceeded the web client bound"
            return None
        with self._lock:
            self._events.append(public)
            occurred = event.get("occurred_at")
            if not replay and isinstance(occurred, int):
                self._updated[key] = max(self._updated.get(key, 0), occurred)
            self._trim_locked()
        return public

    def _trim_locked(self) -> None:
        counts: dict[str, int] = {}
        kept: list[dict[str, Any]] = []
        for event in reversed(self._events):
            counts[event["key"]] = counts.get(event["key"], 0) + 1
            if counts[event["key"]] <= MAX_EVENTS_PER_SESSION:
                kept.append(event)
        kept.reverse()
        self._events = kept[-MAX_EVENTS:]

    # Payloads ----------------------------------------------------------

    def sessions_payload(self) -> dict[str, Any]:
        state = self.client.console()
        now = int(time.time() * 1000)
        with self._lock:
            updated = dict(self._updated)
            notice, epoch = self._notice, self._epoch
        sessions = [
            _session_payload(key, view, updated.get(key, int(view.get("opened_at") or 0)), now)
            for key, view in state.sessions.items()
        ]
        return {
            "epoch": epoch,
            "connected": self.client.connected,
            "notice": notice,
            "focused_session": None if state.focus is None else state.focus.to_dict(),
            "sessions": sessions,
        }

    def snapshot(self) -> dict[str, Any]:
        payload = self.sessions_payload()
        with self._lock:
            payload["events"] = [dict(event) for event in self._events]
        return payload

    # Browser to Bridge -------------------------------------------------

    def api_post(self, path: str, value: object) -> tuple[int, dict[str, Any]] | None:
        if path != "/api/control":
            return None
        try:
            kind, key, payload = _control(value)
        except ValueError:
            return 400, {"error": "invalid_control_request"}
        result = self.client.command(kind, key, payload)
        if result.code == "bridge_unavailable":
            return 503, {"error": "bridge_unavailable", "message": result_text(result.code)}
        return 200, {
            "status": result.status,
            "code": result.code,
            "duplicate": result.duplicate,
            "message": result_text(result.code),
        }

    def api_get(self, request: BaseHTTPRequestHandler, path: str) -> tuple[int, dict[str, Any]] | None:
        if path != "/api/catalog":
            return None
        query = parse_qs(urlsplit(request.path).query)
        try:
            key = _session(*(_single(query, name) for name in ("agent_id", "session_id", "project_id")))
        except ValueError:
            return 400, {"error": "invalid_catalog_request"}
        status, catalog = self.client.catalog(key)
        if status == "bridge_unavailable":
            return 503, {"error": "bridge_unavailable"}
        public: dict[str, Any] = {**key.to_dict(), "available": status == "ok", "notice": ""}
        if status != "ok":
            public["notice"] = "catalog_unavailable"
            return 200, public
        for name in _CATALOG_FIELDS:
            if name in catalog:
                public[name] = catalog[name]
        return 200, public


def _joined(key: SessionKey) -> str:
    return _SEPARATOR.join((key.agent_id, key.session_id, key.project_id))


def _identity(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > MAX_IDENTITY_CHARS
        or any(ord(character) < 32 or character == _SEPARATOR for character in value)
    ):
        raise ValueError("identity is invalid")
    return value


def _session(agent_id: object, session_id: object, project_id: object) -> SessionKey:
    return SessionKey(_identity(agent_id), _identity(session_id), _identity(project_id))


def _single(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name, [])
    if len(values) != 1:
        raise ValueError(f"{name} is required once")
    return values[0]


def _setting(value: dict[str, Any], name: str) -> tuple[bool, str | None]:
    if name not in value:
        return False, None
    item = value[name]
    if item is None:
        return True, None
    if (
        not isinstance(item, str)
        or not item.strip()
        or len(item) > MAX_LABEL_CHARS
        or any(character.isspace() or ord(character) < 32 for character in item.strip())
    ):
        raise ValueError(f"{name} is invalid")
    return True, item.strip()


def _control(value: object) -> tuple[str, SessionKey, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError("control must be an object")
    kind = value.get("kind")
    key = _session(value.get("agent_id"), value.get("session_id"), value.get("project_id"))
    if kind in ("focus", "interrupt"):
        return kind, key, {}
    if kind == "submit_prompt":
        text = value.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError("prompt is invalid")
        return kind, key, {"text": text}
    if kind in ("approve", "reject"):
        # The page names the Bridge approval id as request_id.
        return kind, key, {"approval_id": _identity(value.get("request_id"))}
    if kind == "configure_session":
        payload: dict[str, Any] = {}
        for page_name, name in (("model", "model"), ("reasoning_effort", "effort")):
            present, setting = _setting(value, page_name)
            if present:
                payload[name] = setting
        if not payload:
            raise ValueError("configure_session needs model or reasoning_effort")
        return kind, key, payload
    raise ValueError("unknown control")


def _agent_state(view: dict[str, Any]) -> str:
    if view.get("lifecycle") == "disconnected":
        return ""
    activity = view.get("activity")
    if activity == "idle":
        status = (view.get("last_outcome") or {}).get("status")
        return {"completed": "completed", "failed": "failed"}.get(status, "idle")
    return {"working": "working", "waiting_approval": "waiting_approval", "waiting_input": "waiting_user"}.get(
        str(activity), ""
    )


def _capability_words(matrix: dict[str, Any]) -> list[str]:
    words = ["interaction_events", "submit_prompt"]
    if matrix.get("interrupt"):
        words.append("interrupt")
    if matrix.get("approvals") in ("answerable", "notify_only"):
        words.append("approval_requests")
    if matrix.get("approvals") == "answerable":
        words.append("approval_decisions")
    if matrix.get("resume"):
        words.append("session_resume")
    return words


def _session_payload(key: SessionKey, view: dict[str, Any], updated_at: int, now: int) -> dict[str, Any]:
    runtime = view.get("runtime") if isinstance(view.get("runtime"), dict) else {}
    approvals = [
        ticket
        for ticket in view.get("approvals", [])
        if ticket.get("kind") == "approval" and ticket.get("answerable") and ticket.get("expires_at", 0) > now
    ]
    approval = approvals[0] if approvals else None
    return {
        "key": _joined(key),
        **key.to_dict(),
        "slot": None,
        "lifecycle": view.get("lifecycle") or "",
        "label": _AGENT_LABELS.get(key.agent_id, runtime.get("runtime_name") or key.agent_id),
        "agent_state": _agent_state(view),
        "progress": None,
        "updated_at": updated_at,
        "registered_at": int(view.get("opened_at") or 0),
        "attention": bool(view.get("attention")),
        "adapter_id": runtime.get("adapter_id", ""),
        "runtime_name": runtime.get("runtime_name", ""),
        "runtime_version": runtime.get("runtime_version", ""),
        "capabilities": _capability_words(view.get("capabilities") or {}),
        "approval": None
        if approval is None
        else {"request_id": approval["approval_id"], "summary": approval["summary"], "expires_at": approval["expires_at"]},
    }
