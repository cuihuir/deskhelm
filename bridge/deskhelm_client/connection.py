"""One Bridge connection per surface process (ADR 0043 §4.6).

A surface subscribes to the console and, at the operator tier, to the
interaction stream. The client reconnects with jittered backoff, resumes the
console from its cursor when the same Bridge instance is still running,
hands replayed interaction events over once, and correlates commands and
queries with their results. It makes no business decisions.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
import random
import select
import socket
from threading import Event, Lock, Thread
import time
from types import MappingProxyType
from typing import Any
import uuid

from deskhelm_protocol import PROTOCOL_VERSION, FrameDecoder, SessionKey, Tier, WireError, encode_frame

COMMAND_TTL_MS = 30_000
MAX_PENDING_CALLS = 64
MAX_SEEN_EVENTS = 4096
_POLL_SECONDS = 0.5
_EMPTY: Mapping[SessionKey, dict[str, Any]] = MappingProxyType({})


class _Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ConsoleState:
    """One immutable copy of the console projection."""

    instance_id: str = ""
    seq: int = 0
    focus: SessionKey | None = None
    sessions: Mapping[SessionKey, dict[str, Any]] = field(default_factory=lambda: _EMPTY)

    def session(self, key: SessionKey) -> dict[str, Any] | None:
        return self.sessions.get(key)

    def approvals(self, key: SessionKey, *, now_ms: int | None = None) -> list[dict[str, Any]]:
        """The session's open approvals that can be answered here."""
        view = self.sessions.get(key)
        if view is None:
            return []
        now = int(time.time() * 1000) if now_ms is None else now_ms
        return [
            ticket
            for ticket in view.get("approvals", [])
            if ticket.get("kind") == "approval" and ticket.get("answerable") and ticket.get("expires_at", 0) > now
        ]


@dataclass(frozen=True, slots=True)
class ConsoleChange:
    state: ConsoleState
    previous: ConsoleState
    # A snapshot replaced the state; ``restarted`` means a new Bridge instance.
    reset: bool = False
    restarted: bool = False


@dataclass(frozen=True, slots=True)
class CommandResult:
    command_id: str
    status: str
    code: str
    duplicate: bool = False

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@dataclass(slots=True)
class _Call:
    done: Event = field(default_factory=Event)
    frame: dict[str, Any] | None = None


class BridgeClient:
    def __init__(
        self,
        socket_path: Path,
        *,
        client_id: str,
        tier: Tier = Tier.OPERATOR,
        interaction: bool = False,
        on_console: Callable[[ConsoleChange], None] | None = None,
        on_interaction: Callable[[dict[str, Any], bool], None] | None = None,
        on_connection: Callable[[bool], None] | None = None,
        reconnect_initial: float = 0.2,
        reconnect_max: float = 5.0,
        io_timeout: float = 5.0,
    ) -> None:
        if interaction and tier is not Tier.OPERATOR:
            raise ValueError("only the operator tier reads the interaction stream")
        self.socket_path = socket_path
        self.client_id = client_id
        self.tier = tier
        self.interaction = interaction
        self._on_console = on_console
        self._on_interaction = on_interaction
        self._on_connection = on_connection
        self.reconnect_initial = reconnect_initial
        self.reconnect_max = reconnect_max
        self.io_timeout = io_timeout
        self._lock = Lock()
        self._write_lock = Lock()
        self._stop = Event()
        self._ready = Event()
        self._sock: socket.socket | None = None
        self._state = ConsoleState()
        self._last_instance = ""
        self._interaction_instance = ""
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._calls: dict[str, _Call] = {}
        self._thread: Thread | None = None

    # Lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread = Thread(target=self._run, name="deskhelm-client", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._drop()
        if self._thread is not None:
            self._thread.join(timeout=2)

    @property
    def connected(self) -> bool:
        return self._ready.is_set()

    def wait_ready(self, timeout: float) -> bool:
        """Wait until the console is in sync with a live Bridge."""
        return self._ready.wait(timeout)

    def console(self) -> ConsoleState:
        with self._lock:
            return self._state

    # Requests ----------------------------------------------------------

    def command(
        self,
        kind: str,
        session: SessionKey,
        payload: dict[str, Any] | None = None,
        *,
        command_id: str | None = None,
        idempotency_key: str | None = None,
        ttl_ms: int = COMMAND_TTL_MS,
        timeout: float = 10.0,
    ) -> CommandResult:
        """Send one command and wait for its result. Never retried here."""
        command_id = command_id or uuid.uuid4().hex
        now = int(time.time() * 1000)
        frame = {
            "type": "command",
            "command": {
                "command_id": command_id,
                "kind": kind,
                **session.to_dict(),
                "issued_by": self.client_id,
                "issued_at": now,
                "expires_at": now + ttl_ms,
                "idempotency_key": idempotency_key or command_id,
                "payload": payload or {},
            },
        }
        code, answer = self._call(command_id, frame, timeout)
        if answer is None:
            return CommandResult(command_id, "rejected", code)
        return CommandResult(
            command_id,
            str(answer.get("status") or "rejected"),
            str(answer.get("code") or "dispatch_failed"),
            bool(answer.get("duplicate")),
        )

    def catalog(self, session: SessionKey, *, timeout: float = 5.0) -> tuple[str, dict[str, Any]]:
        """The session's cached catalog: ``ok``, ``unavailable``, or a refusal."""
        query_id = uuid.uuid4().hex
        frame = {"type": "query", "query_id": query_id, "query": "catalog", "session": session.to_dict()}
        code, answer = self._call(query_id, frame, timeout)
        if answer is None:
            return code, {}
        catalog = answer.get("catalog")
        return str(answer.get("status") or "unavailable"), catalog if isinstance(catalog, dict) else {}

    def _call(self, call_id: str, frame: dict[str, Any], timeout: float) -> tuple[str, dict[str, Any] | None]:
        call = _Call()
        with self._lock:
            sock = self._sock
            if sock is None or not self._ready.is_set():
                return "bridge_unavailable", None
            if len(self._calls) >= MAX_PENDING_CALLS:
                return "client_busy", None
            self._calls[call_id] = call
        try:
            with self._write_lock:
                sock.sendall(encode_frame(frame))
        except (OSError, WireError):
            with self._lock:
                self._calls.pop(call_id, None)
            return "bridge_unavailable", None
        if not call.done.wait(timeout):
            with self._lock:
                self._calls.pop(call_id, None)
            # The Bridge may still act on it; the outcome is unknown.
            return "timeout", None
        if call.frame is None:
            return "connection_lost", None
        return "", call.frame

    # Connection loop ---------------------------------------------------

    def _run(self) -> None:
        delay = self.reconnect_initial
        while not self._stop.is_set():
            stable = False
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                # Stored first, so a failed connect is closed with the rest.
                with self._lock:
                    self._sock = sock
                sock.settimeout(self.io_timeout)
                sock.connect(str(self.socket_path))
                decoder = FrameDecoder()
                self._open(sock, decoder)
                stable = True
                self._serve(sock, decoder)
            except (_Refused, OSError, WireError, ValueError):
                pass
            finally:
                self._drop()
            if stable:
                delay = self.reconnect_initial
            # Jitter keeps several surfaces from reconnecting in the same instant.
            if self._stop.wait(delay * random.uniform(0.5, 1.0)):
                return
            delay = min(self.reconnect_max, delay * 2)

    def _open(self, sock: socket.socket, decoder: FrameDecoder) -> None:
        hello = {
            "type": "hello",
            "protocol_version": PROTOCOL_VERSION,
            "role": "client",
            "client_id": self.client_id,
            "tier": self.tier.value,
        }
        sock.sendall(encode_frame(hello))
        while True:
            frames = decoder.feed(_recv(sock))
            refusal = next((frame for frame in frames if frame.get("type") == "error"), None)
            if refusal is not None:
                raise _Refused(str(refusal.get("code") or "error"))
            ok = next((index for index, frame in enumerate(frames) if frame.get("type") == "hello_ok"), None)
            if ok is not None:
                instance, pending = str(frames[ok].get("instance_id") or ""), frames[ok + 1 :]
                break
        with self._lock:
            state = self._state
        resume = bool(state.instance_id) and state.instance_id == instance
        subscribe: dict[str, Any] = {"type": "subscribe", "stream": "console"}
        if resume:
            # The same Bridge is still running: it sends only the deltas this
            # client missed, or a snapshot when it no longer holds them.
            subscribe["after"] = {"instance_id": state.instance_id, "seq": state.seq}
        frames_out = [subscribe]
        if self.interaction:
            frames_out.append({"type": "subscribe", "stream": "interaction"})
        with self._write_lock:
            for frame in frames_out:
                sock.sendall(encode_frame(frame))
        sock.settimeout(None)
        for frame in pending:
            self._dispatch(frame)
        if resume:
            self._mark_ready()

    def _serve(self, sock: socket.socket, decoder: FrameDecoder) -> None:
        while not self._stop.is_set():
            readable, _, _ = select.select([sock], [], [], _POLL_SECONDS)
            if not readable:
                continue
            for frame in decoder.feed(_recv(sock)):
                self._dispatch(frame)

    def _dispatch(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "snapshot" and frame.get("stream") == "console":
            self._console_snapshot(frame)
        elif kind == "delta":
            self._console_delta(frame)
        elif kind == "snapshot" and frame.get("stream") == "interaction":
            instance = str(frame.get("instance_id") or "")
            if instance != self._interaction_instance:
                self._interaction_instance = instance
                self._seen.clear()
        elif kind == "interaction":
            self._interaction(frame)
        elif kind in ("command_result", "query_result"):
            call_id = frame.get("command_id") if kind == "command_result" else frame.get("query_id")
            with self._lock:
                call = self._calls.pop(call_id, None) if isinstance(call_id, str) else None
            if call is not None:
                call.frame = frame
                call.done.set()
        elif kind == "error":
            raise _Refused(str(frame.get("code") or "error"))

    def _console_snapshot(self, frame: dict[str, Any]) -> None:
        body = frame.get("state") if isinstance(frame.get("state"), dict) else {}
        sessions: dict[SessionKey, dict[str, Any]] = {}
        for view in body.get("sessions") or []:
            if isinstance(view, dict):
                sessions[SessionKey.from_dict(view)] = view
        focus = body.get("focus")
        state = ConsoleState(
            instance_id=str(frame.get("instance_id") or ""),
            seq=int(frame.get("seq") or 0),
            focus=SessionKey.from_dict(focus) if isinstance(focus, dict) else None,
            sessions=MappingProxyType(sessions),
        )
        with self._lock:
            previous = self._state
            self._state = state
            restarted = bool(self._last_instance) and self._last_instance != state.instance_id
            self._last_instance = state.instance_id
        self._notify(ConsoleChange(state, previous, reset=True, restarted=restarted))
        self._mark_ready()

    def _console_delta(self, frame: dict[str, Any]) -> None:
        seq = frame.get("seq")
        with self._lock:
            previous = self._state
        if frame.get("instance_id") != previous.instance_id or not isinstance(seq, int):
            raise ValueError("a delta from another Bridge instance")
        if seq <= previous.seq:
            return
        if seq != previous.seq + 1:
            # A gap; reconnecting asks for the missed deltas or a snapshot.
            raise ValueError("console sequence gap")
        sessions = dict(previous.sessions)
        focus = previous.focus
        for change in frame.get("changes") or []:
            op = change.get("op") if isinstance(change, dict) else None
            if op == "session" and isinstance(change.get("session"), dict):
                view = change["session"]
                sessions[SessionKey.from_dict(view)] = view
            elif op == "session_removed":
                sessions.pop(SessionKey.from_dict(change), None)
            elif op == "focus":
                value = change.get("focus")
                focus = SessionKey.from_dict(value) if isinstance(value, dict) else None
        state = ConsoleState(previous.instance_id, seq, focus, MappingProxyType(sessions))
        with self._lock:
            self._state = state
        self._notify(ConsoleChange(state, previous))

    def _interaction(self, frame: dict[str, Any]) -> None:
        event = frame.get("event")
        if not isinstance(event, dict):
            return
        event_id = event.get("event_id")
        if isinstance(event_id, str):
            # A reconnect replays the window; hand each event over once.
            if event_id in self._seen:
                return
            self._seen[event_id] = None
            while len(self._seen) > MAX_SEEN_EVENTS:
                self._seen.popitem(last=False)
        callback = self._on_interaction
        if callback is not None:
            _quietly(callback, event, bool(frame.get("replay")))

    def _notify(self, change: ConsoleChange) -> None:
        callback = self._on_console
        if callback is not None:
            _quietly(callback, change)

    def _mark_ready(self) -> None:
        if not self._ready.is_set():
            self._ready.set()
            callback = self._on_connection
            if callback is not None:
                _quietly(callback, True)

    def _drop(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
            calls = list(self._calls.values())
            self._calls.clear()
            was_ready = self._ready.is_set()
            self._ready.clear()
        for call in calls:
            call.done.set()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if was_ready and self._on_connection is not None:
            _quietly(self._on_connection, False)


def _recv(sock: socket.socket) -> bytes:
    data = sock.recv(65536)
    if not data:
        raise OSError("the Bridge closed the connection")
    return data


def _quietly(callback: Callable[..., None], *args: Any) -> None:
    # A surface's rendering bug must not tear down the shared connection.
    try:
        callback(*args)
    except Exception:
        pass
