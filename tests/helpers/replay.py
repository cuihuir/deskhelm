"""Replay vendor transcripts through the real adapters.

Transcript format ``deskhelm-replay-v1`` is NDJSON. The first line is a header:

    {"type": "header", "format": "deskhelm-replay-v1", "runtime": "grok",
     "scenario": "simple", "source_type": "synthetic", "notes": "..."}

``source_type`` is ``synthetic`` or ``recorded``. Every later line is a step:

- ``inbound``: a frame the vendor sends to the adapter.
- ``outbound``: a frame the adapter must send next. Keys missing from the
  expected frame are ignored and ``"<any>"`` matches any value. A string that
  starts with ``$`` captures the actual value the first time and must match
  it afterwards; inbound frames substitute captured values.
- ``control``: a DeskHelm control routed through a real ControlRouter. Kind
  is ``submit_prompt`` (with ``text``), ``interrupt``, ``approve``, or
  ``reject``. Approvals decide the session's single pending request.
- ``events``: the kinds of interaction events published since the previous
  ``events`` step, in order. ``error_code`` checks the last event's task code.

Only the vendor transport is replaced, so a replay exercises the adapter's
own parsing, correlation, and control handling.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from queue import Empty, Queue
import select
import socket
import sys
import tempfile
import time
from threading import Condition, Lock
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for _kind in ("codex", "claude", "grok"):
    _adapter = ROOT / "adapters" / _kind
    if str(_adapter) not in sys.path:
        sys.path.insert(0, str(_adapter))

from deskhelm_bridge.control import (  # noqa: E402
    ApprovalDecisionPayload,
    ControlCommand,
    ControlKind,
    InterruptPayload,
    SubmitPromptPayload,
)
from deskhelm_bridge.control_result import ControlResultCode  # noqa: E402
from deskhelm_bridge.control_router import ControlRouter  # noqa: E402
from deskhelm_bridge.interaction import InteractionEvent, TaskPayload  # noqa: E402
from deskhelm_bridge.runtime_directory import (  # noqa: E402
    RUNTIME_KIND_CLAUDE,
    RUNTIME_KIND_GROK,
    RuntimeAttachSpec,
)
from deskhelm_bridge.session_registry import SessionKey, SessionRegistry  # noqa: E402


TRANSCRIPT_FORMAT = "deskhelm-replay-v1"
STEP_TYPES = frozenset({"inbound", "outbound", "control", "events"})
SOURCE_TYPES = frozenset({"synthetic", "recorded"})
STEP_TIMEOUT_SECONDS = 2.0


class ReplayMismatch(AssertionError):
    """The adapter diverged from the transcript."""


@dataclass(frozen=True)
class Transcript:
    path: Path
    header: dict[str, Any]
    steps: tuple[dict[str, Any], ...]


def load_transcript(path: Path) -> Transcript:
    lines = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError(f"{path.name}: transcript is empty")
    header, *steps = lines
    if header.get("type") != "header" or header.get("format") != TRANSCRIPT_FORMAT:
        raise ValueError(f"{path.name}: header must name {TRANSCRIPT_FORMAT}")
    for key in ("runtime", "scenario", "source_type", "notes"):
        value = header.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{path.name}: header needs {key}")
    if header["source_type"] not in SOURCE_TYPES:
        raise ValueError(f"{path.name}: unknown source_type")
    for index, step in enumerate(steps, start=2):
        if step.get("type") not in STEP_TYPES:
            raise ValueError(f"{path.name}:{index}: unknown step type")
    return Transcript(path, header, tuple(steps))


class Bindings:
    """Values captured from ``$name`` placeholders."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def match(self, expected: Any, actual: Any, where: str) -> None:
        if expected == "<any>":
            return
        if isinstance(expected, str) and expected.startswith("$"):
            if expected not in self.values:
                self.values[expected] = actual
            elif self.values[expected] != actual:
                raise ReplayMismatch(
                    f"{where}: {expected} was {self.values[expected]!r}, got {actual!r}"
                )
            return
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                raise ReplayMismatch(f"{where}: expected an object, got {actual!r}")
            for key, value in expected.items():
                if key not in actual:
                    raise ReplayMismatch(f"{where}: missing key {key!r} in {actual!r}")
                self.match(value, actual[key], f"{where}.{key}")
            return
        if isinstance(expected, list):
            if not isinstance(actual, list) or len(actual) != len(expected):
                raise ReplayMismatch(f"{where}: expected {expected!r}, got {actual!r}")
            for index, (left, right) in enumerate(zip(expected, actual)):
                self.match(left, right, f"{where}[{index}]")
            return
        if expected != actual:
            raise ReplayMismatch(f"{where}: expected {expected!r}, got {actual!r}")

    def substitute(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            if value not in self.values:
                raise ReplayMismatch(f"{value} is used before it is captured")
            return self.values[value]
        if isinstance(value, dict):
            return {key: self.substitute(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.substitute(item) for item in value]
        return value


class _ReplayRun:
    """Walk one transcript; subclasses own the vendor transport."""

    def __init__(self, transcript: Transcript, session: SessionKey) -> None:
        self.transcript = transcript
        self.session = session
        registry = SessionRegistry(slot_count=1)
        registry.register(session, preferred_slot=0)
        self.router = ControlRouter(registry)
        self.bindings = Bindings()
        self._events: list[InteractionEvent] = []
        self._events_lock = Lock()
        self._checked = 0

    def play(self) -> None:
        try:
            for index, step in enumerate(self.transcript.steps, start=2):
                where = f"{self.transcript.path.name}:{index} {step['type']}"
                kind = step["type"]
                if kind == "inbound":
                    self.send(self.bindings.substitute(step["frame"]))
                elif kind == "outbound":
                    self.bindings.match(step["frame"], self.receive(where), where)
                elif kind == "control":
                    self.settle(where)
                    self._control(step, index, where)
                else:
                    self._check_events(step, where)
            self._check_finished()
        finally:
            self.close()

    def publish(self, event: InteractionEvent) -> None:
        self.router.observe_interaction(event)
        with self._events_lock:
            self._events.append(event)

    def send(self, frame: dict[str, Any]) -> None:
        raise NotImplementedError

    def settle(self, where: str) -> None:
        """Wait until the adapter has handled every inbound frame sent so far."""

    def receive(self, where: str) -> dict[str, Any]:
        raise NotImplementedError

    def pending_outbound(self) -> dict[str, Any] | None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _control(self, step: dict[str, Any], index: int, where: str) -> None:
        kind = ControlKind(step["kind"])
        now = int(time.time() * 1000)
        expires_at = now + 60_000
        if kind is ControlKind.SUBMIT_PROMPT:
            payload: Any = SubmitPromptPayload(step["text"])
        elif kind is ControlKind.INTERRUPT:
            payload = InterruptPayload("replay")
        elif kind in {ControlKind.APPROVE, ControlKind.REJECT}:
            pending = self.router.pending_approvals_for(self.session, now_ms=now)
            if len(pending) != 1:
                raise ReplayMismatch(f"{where}: expected one pending approval, found {len(pending)}")
            payload = ApprovalDecisionPayload(
                request_id=pending[0].request_id,
                summary=pending[0].summary,
                request_expires_at=pending[0].expires_at,
            )
            expires_at = pending[0].expires_at
        else:
            raise ReplayMismatch(f"{where}: unsupported control {kind.value}")
        command = ControlCommand(
            kind=kind,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            issued_by="replay",
            issued_at=now,
            expires_at=expires_at,
            idempotency_key=f"replay-{index}",
            payload=payload,
        )
        # An adapter that is still attaching answers handler_unavailable. That
        # result is not retained, so the same command may be sent again.
        deadline = time.monotonic() + STEP_TIMEOUT_SECONDS
        while True:
            result = self.router.route(command, now_ms=int(time.time() * 1000))
            if (
                result.code is not ControlResultCode.HANDLER_UNAVAILABLE
                or time.monotonic() >= deadline
            ):
                break
            time.sleep(0.01)
        if result.code is not ControlResultCode.DISPATCHED:
            raise ReplayMismatch(f"{where}: control answered {result.code.value}")

    def _check_events(self, step: dict[str, Any], where: str) -> None:
        expected = list(step["kinds"])
        target = self._checked + len(expected)
        deadline = time.monotonic() + STEP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            with self._events_lock:
                if len(self._events) >= target:
                    break
            time.sleep(0.01)
        with self._events_lock:
            window = self._events[self._checked:target]
        kinds = [event.kind.value for event in window]
        if kinds != expected:
            raise ReplayMismatch(f"{where}: expected events {expected}, got {kinds}")
        code = step.get("error_code")
        if code is not None:
            payload = window[-1].payload
            actual = payload.error_code if isinstance(payload, TaskPayload) else None
            if actual != code:
                raise ReplayMismatch(f"{where}: expected error_code {code!r}, got {actual!r}")
        self._checked = target

    def _check_finished(self) -> None:
        extra = self.pending_outbound()
        if extra is not None:
            raise ReplayMismatch(
                f"{self.transcript.path.name}: unexpected outbound frame {extra!r}"
            )
        with self._events_lock:
            unchecked = [event.kind.value for event in self._events[self._checked:]]
        if unchecked:
            raise ReplayMismatch(
                f"{self.transcript.path.name}: unchecked events {unchecked}"
            )


class _RouterFacade:
    def __init__(self, router: ControlRouter) -> None:
        self._router = router

    def register_handler(self, kind, handler):
        return self._router.register_handler(kind, handler)

    def register_catalog_provider(self, provider):
        return self._router.register_catalog_provider(provider)

    def resolve_approval(self, request_id: str, *, now_ms: int | None = None) -> bool:
        return self._router.resolve_approval(request_id, now_ms=now_ms)


class _StdioReplay(_ReplayRun):
    """Act as the stdio relay peer of a Claude or Grok runtime."""

    def __init__(self, transcript: Transcript, runtime_class, runtime_kind: str) -> None:
        super().__init__(
            transcript, SessionKey(transcript.header["runtime"], "replay", "demo")
        )
        self._directory = tempfile.TemporaryDirectory()
        path = Path(self._directory.name) / "relay.sock"
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(path))
        self._listener.listen(1)
        spec = RuntimeAttachSpec(
            session=self.session,
            source_version="replay",
            runtime_kind=runtime_kind,
            relay_socket=path,
        )
        self._runtime = runtime_class(spec)
        self._runtime.start(self.publish, _RouterFacade(self.router))
        self._peer, _ = self._listener.accept()
        self._buffer = bytearray()

    def send(self, frame: dict[str, Any]) -> None:
        self._peer.sendall(json.dumps(frame, ensure_ascii=False).encode("utf-8") + b"\n")

    def receive(self, where: str) -> dict[str, Any]:
        line = self._next_line(STEP_TIMEOUT_SECONDS)
        if line is None:
            raise ReplayMismatch(f"{where}: the adapter sent nothing")
        return json.loads(line)

    def pending_outbound(self) -> dict[str, Any] | None:
        line = self._next_line(0)
        return json.loads(line) if line is not None else None

    def _next_line(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buffer:
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([self._peer], [], [], remaining)
            if not readable:
                return None
            chunk = self._peer.recv(65536)
            if not chunk:
                return None
            self._buffer.extend(chunk)
        index = self._buffer.index(b"\n")
        line = bytes(self._buffer[:index])
        del self._buffer[: index + 1]
        return line

    def close(self) -> None:
        self._runtime.close()
        self._peer.close()
        self._listener.close()
        self._directory.cleanup()


class _QueueTransport:
    """An app-server transport whose frames come from the transcript.

    The adapter reads and handles frames on one thread, so its next
    receive_message call proves every frame it took before is handled.
    """

    def __init__(self) -> None:
        self.incoming: Queue[dict[str, Any]] = Queue()
        self.outgoing: Queue[dict[str, Any]] = Queue()
        self._handled = Condition()
        self._returned = 0
        self._handled_count = 0

    def connect(self) -> None:
        return None

    def send_message(self, message: dict[str, Any]) -> None:
        self.outgoing.put(message)

    def receive_message(self, timeout_seconds: float) -> dict[str, Any] | None:
        with self._handled:
            self._handled_count = self._returned
            self._handled.notify_all()
        try:
            message = self.incoming.get(timeout=timeout_seconds)
        except Empty:
            return None
        with self._handled:
            self._returned += 1
        return message

    def wait_until_handled(self, count: int, timeout: float) -> bool:
        with self._handled:
            return self._handled.wait_for(
                lambda: self._handled_count >= count, timeout
            )

    def close(self) -> None:
        return None


class _CodexReplay(_ReplayRun):
    """Act as the Codex app-server for the in-process adapter."""

    def __init__(self, transcript: Transcript) -> None:
        super().__init__(transcript, SessionKey("codex", "replay", "demo"))
        from deskhelm_codex_adapter.app_server import CodexAppServerAdapter

        self._transport = _QueueTransport()
        # A long discovery interval keeps the outbound order deterministic.
        self._adapter = CodexAppServerAdapter(
            socket_path=Path("/tmp/deskhelm-codex-replay.sock"),
            session=self.session,
            source_version="replay",
            transport_factory=lambda: self._transport,
            request_timeout_seconds=STEP_TIMEOUT_SECONDS,
            discovery_interval_seconds=3600,
            reconnect_initial_seconds=0.01,
            reconnect_max_seconds=0.02,
        )
        self._adapter.start(self.publish, self.router)
        self._sent = 0

    def send(self, frame: dict[str, Any]) -> None:
        self._sent += 1
        self._transport.incoming.put(frame)

    def settle(self, where: str) -> None:
        if not self._transport.wait_until_handled(self._sent, STEP_TIMEOUT_SECONDS):
            raise ReplayMismatch(f"{where}: the adapter did not handle earlier frames")

    def receive(self, where: str) -> dict[str, Any]:
        try:
            return self._transport.outgoing.get(timeout=STEP_TIMEOUT_SECONDS)
        except Empty as error:
            raise ReplayMismatch(f"{where}: the adapter sent nothing") from error

    def pending_outbound(self) -> dict[str, Any] | None:
        try:
            return self._transport.outgoing.get_nowait()
        except Empty:
            return None

    def close(self) -> None:
        self._adapter.close()


def replay_transcript(transcript: Transcript) -> None:
    runtime = transcript.header["runtime"]
    if runtime == "codex":
        run: _ReplayRun = _CodexReplay(transcript)
    elif runtime == "claude":
        from deskhelm_claude_adapter.runtime import ClaudeRelayRuntime

        run = _StdioReplay(transcript, ClaudeRelayRuntime, RUNTIME_KIND_CLAUDE)
    elif runtime == "grok":
        from deskhelm_grok_adapter.runtime import GrokRelayRuntime

        run = _StdioReplay(transcript, GrokRelayRuntime, RUNTIME_KIND_GROK)
    else:
        raise ValueError(f"no replay runner for runtime {runtime!r}")
    run.play()
