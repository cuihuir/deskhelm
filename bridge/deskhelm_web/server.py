"""The loopback web surface on protocol v2 (ADR 0036, ADR 0043, ADR 0044).

One operator connection through deskhelm_client feeds every browser. The
server folds each session's interaction events into a transcript
(``deskhelm_client.transcript``) and sends a browser one snapshot of the
sessions and their transcripts, then ordered events: ``sessions`` when the
console changes and ``patch`` when a transcript does. Every event carries a
revision, so a page drops what its snapshot already holds. The Bridge's
bounded interaction window arrives on connect, so a refreshed page shows
recent activity and a reconnecting page still sees pending approvals.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import re
from pathlib import Path
from threading import Lock
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit
import uuid

from deskhelm_client import BridgeClient, ConsoleChange, agent_label, result_text
from deskhelm_client.texts import ACTIVITY_TEXT, FAILURE_TEXT, OUTCOME_TEXT, RESULT_TEXT, TOOL_TEXT
from deskhelm_client.transcript import Transcript
from deskhelm_protocol import SessionKey
from deskhelm_protocol.model import MAX_IDENTITY_CHARS, MAX_LABEL_CHARS, MAX_TEXT_CHARS

from .http import WebHttp

# More than the Bridge's session limit, so a session that just left still fits.
MAX_TRANSCRIPTS = 64
# The shared wording travels with each snapshot, so the page keeps no copy.
WORDS = {
    "result": RESULT_TEXT,
    "activity": ACTIVITY_TEXT,
    "outcome": OUTCOME_TEXT,
    "failure": FAILURE_TEXT,
    "tool": TOOL_TEXT,
}
_SEPARATOR = "\u001f"
_COMMAND_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_CATALOG_FIELDS = (
    "agent_capabilities",
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
        self._rev = 0
        self._notice = ""
        self._transcripts: dict[SessionKey, Transcript] = {}
        self._previews: dict[SessionKey, str] = {}
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
        with self._lock:
            if change.restarted:
                # A new Bridge holds none of the old activity.
                self._epoch = uuid.uuid4().hex
                self._transcripts, self._previews, self._updated = {}, {}, {}
                self._notice = "bridge_restarted"
            for key, view in change.state.sessions.items():
                # A session's time is when it opened or last did something in its
                # transcript; a change of state, such as a reconnect, is not activity.
                self._updated[key] = max(self._updated.get(key, 0), int(view.get("opened_at") or 0))
            for key in [key for key in self._updated if key not in change.state.sessions]:
                del self._updated[key]
                self._transcripts.pop(key, None)
                self._previews.pop(key, None)
            settled = []
            for key, view in change.state.sessions.items():
                before = change.previous.sessions.get(key)
                left = view.get("lifecycle") == "disconnected" and (before is None or before.get("lifecycle") != "disconnected")
                if left and key in self._transcripts:
                    settled.extend((key, patch) for patch in self._transcripts[key].settle())
            if change.restarted:
                self._emit_locked("snapshot", self._snapshot_locked())
            else:
                self._emit_locked("sessions", self._sessions_locked())
            for key, patch in settled:
                self._emit_locked("patch", {"key": _joined(key), **patch})

    def _on_interaction(self, event: dict[str, Any], replay: bool) -> None:
        try:
            key = SessionKey.from_dict(event)
        except ValueError:
            return
        view = self.client.console().sessions.get(key)
        with self._lock:
            transcript = self._transcripts.get(key)
            if transcript is None:
                if len(self._transcripts) >= MAX_TRANSCRIPTS:
                    if self._notice != "too_many_sessions":
                        self._notice = "too_many_sessions"
                        self._emit_locked("sessions", self._sessions_locked())
                    return
                transcript = self._transcripts[key] = Transcript()
            patches = transcript.apply(event)
            if view is not None and view.get("lifecycle") == "disconnected":
                # Replayed after its host left: nothing of it is still running.
                patches += transcript.settle()
            if not patches:
                return
            occurred = event.get("occurred_at")
            if isinstance(occurred, int) and not isinstance(occurred, bool):
                self._updated[key] = max(self._updated.get(key, 0), occurred)
            preview = transcript.preview()
            changed = preview != self._previews.get(key, "")
            self._previews[key] = preview
            for index, patch in enumerate(patches):
                payload = {"key": _joined(key), **patch}
                if index == len(patches) - 1:
                    payload["updated_at"] = self._updated.get(key, 0)
                    if changed:
                        payload["preview"] = preview
                self._emit_locked("patch", payload)

    def _on_connection(self, connected: bool) -> None:
        with self._lock:
            if not connected:
                self._notice = "bridge_unavailable"
            elif self._notice == "bridge_unavailable":
                self._notice = ""
            self._emit_locked("sessions", self._sessions_locked())

    def _emit_locked(self, name: str, payload: dict[str, Any]) -> None:
        # One lock orders revisions, broadcasts, and snapshots.
        self._rev += 1
        payload["rev"] = self._rev
        self.broadcast(name, payload)

    # Payloads ----------------------------------------------------------

    def _sessions_locked(self) -> dict[str, Any]:
        state = self.client.console()
        now = int(time.time() * 1000)
        return {
            "rev": self._rev,
            "epoch": self._epoch,
            "connected": self.client.connected,
            "notice": self._notice,
            "focus": None if state.focus is None else _joined(state.focus),
            "sessions": [
                _session_view(key, view, self._updated.get(key, 0), self._previews.get(key, ""), now)
                for key, view in state.sessions.items()
            ],
        }

    def _snapshot_locked(self) -> dict[str, Any]:
        payload = self._sessions_locked()
        payload["transcripts"] = {_joined(key): transcript.items() for key, transcript in self._transcripts.items()}
        payload["words"] = WORDS
        return payload

    def sessions_payload(self) -> dict[str, Any]:
        with self._lock:
            return self._sessions_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    # Browser to Bridge -------------------------------------------------

    def api_post(self, path: str, value: object) -> tuple[int, dict[str, Any]] | None:
        if path != "/api/control":
            return None
        try:
            kind, key, payload, command_id = _control(value)
        except ValueError:
            return 400, {"error": "invalid_control_request"}
        result = self.client.command(kind, key, payload, command_id=command_id)
        if result.code == "bridge_unavailable":
            return 503, {"error": "bridge_unavailable", "message": result_text(result.code)}
        return 200, {
            "command_id": result.command_id,
            "status": result.status,
            "code": result.code,
            "duplicate": result.duplicate,
            "message": result_text(result.code),
        }

    def api_get(self, request: BaseHTTPRequestHandler, path: str) -> tuple[int, dict[str, Any]] | None:
        if path == "/api/ping":
            # A cheap answer that tells a page whether its sign-in still holds.
            return 200, {"ok": True}
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


def _control(value: object) -> tuple[str, SessionKey, dict[str, Any], str | None]:
    if not isinstance(value, dict):
        raise ValueError("control must be an object")
    kind = value.get("kind")
    key = _session(value.get("agent_id"), value.get("session_id"), value.get("project_id"))
    command_id = value.get("command_id")
    if command_id is not None and (not isinstance(command_id, str) or not _COMMAND_ID.fullmatch(command_id)):
        raise ValueError("command_id is invalid")
    if kind in ("focus", "interrupt"):
        return kind, key, {}, command_id
    if kind == "submit_prompt":
        text = value.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError("prompt is invalid")
        return kind, key, {"text": text}, command_id
    if kind in ("approve", "reject"):
        return kind, key, {"approval_id": _identity(value.get("approval_id"))}, command_id
    if kind == "configure_session":
        payload: dict[str, Any] = {}
        for name in ("model", "effort"):
            present, setting = _setting(value, name)
            if present:
                payload[name] = setting
        if not payload:
            raise ValueError("configure_session needs model or effort")
        return kind, key, payload, command_id
    raise ValueError("unknown control")


def _session_view(key: SessionKey, view: dict[str, Any], updated_at: int, preview: str, now: int) -> dict[str, Any]:
    runtime = view.get("runtime") if isinstance(view.get("runtime"), dict) else {}
    outcome = view.get("last_outcome") if isinstance(view.get("last_outcome"), dict) else None
    return {
        "key": _joined(key),
        **key.to_dict(),
        "label": agent_label(key.agent_id, runtime.get("runtime_name", "")),
        "runtime": {"name": runtime.get("runtime_name", ""), "version": runtime.get("runtime_version", "")},
        "lifecycle": view.get("lifecycle") or "",
        "activity": view.get("activity") or "",
        "attention": bool(view.get("attention")),
        "outcome": None if outcome is None else {"status": outcome.get("status"), "failure": outcome.get("failure")},
        "capabilities": view.get("capabilities") if isinstance(view.get("capabilities"), dict) else {},
        "approvals": [
            {name: ticket.get(name) for name in ("approval_id", "kind", "summary", "expires_at", "answerable")}
            for ticket in view.get("approvals", [])
            if ticket.get("expires_at", 0) > now
        ],
        "opened_at": int(view.get("opened_at") or 0),
        "updated_at": max(updated_at, int(view.get("opened_at") or 0)),
        "catalog_rev": int(view.get("catalog_rev") or 0),
        "preview": preview,
    }
