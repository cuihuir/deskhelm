"""Codex app-server driver for a session host (ADR 0043).

The host owns the Codex app-server and the managed TUI. This driver wraps the
app-server adapter and its TUI approval proxy (ADR 0033) unchanged: adapter
interaction events become facts, and effects become adapter controls. Tool
labels are fixed item kinds; command lines, paths, and output stay in fact
content, which only the operator tier receives.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from threading import Event, Lock, Thread, local
import time
from typing import Any, Callable
import uuid

from deskhelm_host.driver import FactSink, SessionState
from deskhelm_protocol import (
    ApprovalOverflow,
    ApprovalSupport,
    CapabilityMatrix,
    EffectKind,
    EffectOutcome,
    FactKind,
    Failure,
    InputSupport,
    OpenRequest,
    PromptWhileBusy,
    RequestKind,
    ResolvedBy,
    RuntimeIdentity,
    SessionKey,
)
from deskhelm_protocol.model import (
    MAX_ANSWER_CHARS,
    MAX_CATALOG_BYTES,
    MAX_DETAIL_CHARS,
    MAX_IDENTITY_CHARS,
    MAX_OPEN_REQUESTS,
    MAX_OPTIONS,
    MAX_TEXT_CHARS,
    ProtocolViolation,
    questions_field,
)

from .app_server import CodexAppServerAdapter
from .controls import (
    AnswerInputPayload,
    ApprovalDecisionPayload,
    ConfigureSessionPayload,
    ControlCommand,
    ControlKind,
    ControlUndelivered,
    InterruptPayload,
    SessionKey as AdapterSessionKey,
    SubmitPromptPayload,
)
from .events import (
    InteractionEvent,
    InteractionKind,
    MessagePhase as AdapterMessagePhase,
    MessageRole as AdapterMessageRole,
    ToolPhase as AdapterToolPhase,
)

ISSUER = "deskhelm-host"
COMMAND_WINDOW_MS = 60_000
# The adapter's default; it does not report when an input request expires.
INPUT_WINDOW_MS = 300_000
# How long an async question, which Codex does not wait on, stays answerable.
ASYNC_INPUT_WINDOW_MS = 3_600_000
# The adapter accepts a model choice for 120 seconds after a catalog read.
CATALOG_FRESH_SECONDS = 110.0
CATALOG_REFRESH_SECONDS = 60.0
WATCH_SECONDS = 0.5

_TOOL_LABELS = {
    "commandExecution": "command",
    "fileChange": "file_change",
    "webSearch": "web_search",
    "mcpToolCall": "mcp",
}
_MESSAGE_PHASES = {AdapterMessagePhase.DELTA: "delta", AdapterMessagePhase.COMPLETE: "complete"}
# Codes that end the turn whichever command they correlate with.
_TURN_ENDINGS = {"interrupted": ("interrupted", None), "codex_turn_failed": ("failed", Failure.PROVIDER)}
# Codes that end the turn only when they name the command that opened it.
_COMMAND_FAILURES = {
    "codex_control_failed": Failure.PROVIDER,
    "codex_control_timeout": Failure.TIMEOUT,
    "codex_connection_lost": Failure.TRANSPORT_LOST,
}
_ITEM = local()


def _ref(value: object) -> str:
    """A valid protocol identity; an overlong or odd vendor id is hashed."""
    text = value if isinstance(value, str) else ""
    if text and text == text.strip() and len(text) <= MAX_IDENTITY_CHARS and all(ord(c) >= 32 for c in text):
        return text
    return "h-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _noop() -> None:
    return None


class _ItemAwareAdapter(CodexAppServerAdapter):
    """Tells the driver which app-server item type a tool event came from."""

    def _publish_thread_item(self, method: str, params_value: object) -> None:
        item = params_value.get("item") if isinstance(params_value, dict) else None
        item_type = item.get("type") if isinstance(item, dict) else None
        _ITEM.label = _TOOL_LABELS.get(item_type, "tool") if isinstance(item_type, str) else "tool"
        try:
            CodexAppServerAdapter._publish_thread_item(self, method, params_value)
        finally:
            _ITEM.label = "tool"


class _Router:
    """The adapter registers v1 handlers here; the driver calls it directly."""

    def __init__(self, resolved: Callable[[str], None]) -> None:
        self._resolved = resolved

    def register_handler(self, kind: ControlKind, handler: Callable[[ControlCommand], None]) -> Callable[[], None]:
        return _noop

    def register_catalog_provider(self, provider: Callable[..., dict[str, Any]]) -> Callable[[], None]:
        return _noop

    def resolve_approval(self, request_id: str) -> None:
        self._resolved(request_id)


@dataclass(frozen=True, slots=True)
class _Turn:
    ref: str
    command_id: str | None


class CodexSession:
    def __init__(
        self,
        session: SessionKey,
        *,
        socket_path: Path,
        runtime_version: str,
        adapter_version: str = "0.2.0",
        tui_proxy_socket_path: Path | None = None,
        approvals: ApprovalSupport = ApprovalSupport.ANSWERABLE,
        refresh_catalog: bool = True,
        clock: Callable[[], int] = lambda: int(time.time() * 1000),
        **adapter_options: Any,
    ) -> None:
        self.runtime = RuntimeIdentity("deskhelm-codex", adapter_version, "codex-cli", runtime_version)
        self.capabilities = CapabilityMatrix(
            version=1,
            prompt_while_busy=PromptWhileBusy.STEER,
            interrupt=True,
            approvals=approvals,
            # Approvals past the adapter's bound stay answerable in the TUI.
            approval_overflow=ApprovalOverflow.NATIVE_UI,
            user_input=InputSupport.ANSWERABLE,
            resume=True,
            configure=frozenset({"model", "effort"}),
            catalog=frozenset({"models", "mcp", "skills"}),
        )
        self._key = AdapterSessionKey(session.agent_id, session.session_id, session.project_id)
        self._adapter = _ItemAwareAdapter(
            socket_path=socket_path,
            session=self._key,
            source_version=runtime_version,
            client_version=adapter_version,
            tui_proxy_socket_path=tui_proxy_socket_path,
            **adapter_options,
        )
        self._refresh_enabled = refresh_catalog
        self._clock = clock
        self._lock = Lock()
        self._emit: FactSink = lambda kind, meta=None, content=None: None
        self._closed = Event()
        self._ready = False
        self._turn: _Turn | None = None
        # A submit in flight that opens the turn if Codex answers before it returns.
        self._pending_turn: str | None = None
        self._submitting: str | None = None
        self._attention = False
        self._requests: dict[str, OpenRequest] = {}
        self._catalog: dict[str, Any] | None = None
        self._catalog_at = 0.0
        self._refreshing = False
        self._watcher: Thread | None = None

    # DriverSession -----------------------------------------------------

    def start(self, emit: FactSink) -> None:
        self._emit = emit
        self._adapter.start(self._on_event, _Router(self._on_resolved))
        self._watcher = Thread(target=self._watch, name="deskhelm-codex-watch", daemon=True)
        self._watcher.start()

    def perform(self, kind: EffectKind, payload: dict[str, Any]) -> EffectOutcome:
        if kind is EffectKind.SUBMIT:
            return self._submit(payload["command_id"], payload["text"])
        if kind is EffectKind.INTERRUPT:
            return self._call(self._adapter.interrupt, ControlKind.INTERRUPT, InterruptPayload(), payload["command_id"])
        if kind is EffectKind.RESPOND:
            return self._respond(payload["command_id"], payload["provider_ref"], payload["decision"] == "approve")
        if kind is EffectKind.ANSWER:
            return self._answer(payload["command_id"], payload["provider_ref"], payload["answers"])
        if kind is EffectKind.CONFIGURE:
            return self._configure(payload)
        return EffectOutcome.UNSUPPORTED

    def state(self) -> SessionState:
        with self._lock:
            now = self._clock()
            return SessionState(
                ready=self._ready,
                turn_ref=self._turn.ref if self._turn else None,
                attention=self._attention,
                open_requests=tuple(request for request in self._requests.values() if request.expires_at > now),
                catalog=self._catalog,
            )

    def wait(self) -> int:
        self._closed.wait()
        return 0

    def close(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
        self._adapter.close()
        with self._lock:
            if self._turn is not None:
                self._finish_turn_locked(
                    {"status": "failed", "failure": Failure.PROCESS_EXIT.value, "reusable": False}, ""
                )
            self._emit(FactKind.SESSION_ENDED, {"reason": "codex exited"})

    # Effects -----------------------------------------------------------

    def _submit(self, command_id: str, text: str) -> EffectOutcome:
        with self._lock:
            if self._turn is None and self._pending_turn is None:
                self._pending_turn = command_id
            self._submitting = command_id
            inputs = [ref for ref, request in self._requests.items() if request.kind is RequestKind.INPUT and request.blocking]
        outcome = EffectOutcome.UNSUPPORTED
        answered = False
        try:
            payload = SubmitPromptPayload(text)
            # The adapter answers a single pending Codex question with the
            # prompt instead of steering.
            before = self._adapter.pending_user_input_count() if inputs else 0
            outcome = self._call(self._adapter.submit_prompt, ControlKind.SUBMIT_PROMPT, payload, command_id)
            answered = outcome is EffectOutcome.ACCEPTED and before == 1 and self._adapter.pending_user_input_count() == 0
        except ValueError:
            pass
        finally:
            with self._lock:
                if self._submitting == command_id:
                    self._submitting = None
                if self._pending_turn == command_id:
                    self._pending_turn = None
                    if outcome is EffectOutcome.ACCEPTED and self._turn is None:
                        self._open_turn_locked(command_id, command_id)
                if answered:
                    for ref in inputs:
                        self._settle_locked(ref, ResolvedBy.DESKHELM)
        return outcome

    def _respond(self, command_id: str, provider_ref: str, allow: bool) -> EffectOutcome:
        with self._lock:
            request = self._requests.get(provider_ref)
        if request is None or request.kind is not RequestKind.APPROVAL:
            return EffectOutcome.UNDELIVERED
        payload = ApprovalDecisionPayload(
            request_id=provider_ref, summary=request.summary, request_expires_at=request.expires_at
        )
        kind, handler = (
            (ControlKind.APPROVE, self._adapter.approve) if allow else (ControlKind.REJECT, self._adapter.reject)
        )
        outcome = self._call(handler, kind, payload, command_id, expires_at=request.expires_at)
        if outcome is EffectOutcome.ACCEPTED:
            with self._lock:
                self._requests.pop(provider_ref, None)
        return outcome

    def _answer(self, command_id: str, provider_ref: str, answers: dict[str, list[str]]) -> EffectOutcome:
        with self._lock:
            request = self._requests.get(provider_ref)
        if request is None or request.kind is not RequestKind.INPUT:
            return EffectOutcome.UNDELIVERED
        payload = AnswerInputPayload(provider_ref, {question_id: tuple(items) for question_id, items in answers.items()})
        outcome = self._call(self._adapter.answer_input, ControlKind.ANSWER_INPUT, payload, command_id)
        if outcome is EffectOutcome.ACCEPTED:
            with self._lock:
                self._requests.pop(provider_ref, None)
        return outcome

    def _configure(self, payload: dict[str, Any]) -> EffectOutcome:
        with self._lock:
            fresh = self._catalog_at > 0 and time.monotonic() - self._catalog_at < CATALOG_FRESH_SECONDS
        if not fresh:
            # The choice is checked against a recent catalog; read one so a
            # retry can succeed.
            self._refresh_catalog(force=True)
            return EffectOutcome.UNDELIVERED
        try:
            config = ConfigureSessionPayload(
                model_set="model" in payload,
                model=payload.get("model") or "",
                effort_set="effort" in payload,
                reasoning_effort=payload.get("effort") or "",
            )
            command = self._command(ControlKind.CONFIGURE_SESSION, config, payload["command_id"])
            self._adapter.configure_session(command)
        except ControlUndelivered:
            return EffectOutcome.UNDELIVERED
        except (RuntimeError, ValueError):
            return EffectOutcome.UNSUPPORTED
        self._refresh_catalog(force=True)
        return EffectOutcome.ACCEPTED

    def _command(self, kind: ControlKind, payload: Any, command_id: str, expires_at: int | None = None) -> ControlCommand:
        now = int(time.time() * 1000)
        return ControlCommand(
            kind=kind,
            agent_id=self._key.agent_id,
            session_id=self._key.session_id,
            project_id=self._key.project_id,
            issued_by=ISSUER,
            issued_at=now,
            expires_at=expires_at or now + COMMAND_WINDOW_MS,
            idempotency_key=command_id,
            payload=payload,
            command_id=command_id,
        )

    def _call(
        self,
        handler: Callable[[ControlCommand], None],
        kind: ControlKind,
        payload: Any,
        command_id: str,
        *,
        expires_at: int | None = None,
    ) -> EffectOutcome:
        try:
            command = self._command(kind, payload, command_id, expires_at)
        except ValueError:
            # An approval that expired while its decision was on the way.
            return EffectOutcome.UNDELIVERED
        try:
            handler(command)
        except ValueError:
            return EffectOutcome.UNSUPPORTED
        except RuntimeError:
            # The adapter refuses before anything reaches Codex, including
            # ControlUndelivered: no thread yet, a full queue, no active turn.
            return EffectOutcome.UNDELIVERED
        return EffectOutcome.ACCEPTED

    # Adapter output ----------------------------------------------------

    def _on_event(self, event: InteractionEvent) -> None:
        refresh = False
        with self._lock:
            if self._closed.is_set():
                return
            kind = event.kind
            if kind is InteractionKind.MESSAGE:
                self._message_locked(event)
            elif kind is InteractionKind.TOOL:
                self._tool_locked(event)
            elif kind is InteractionKind.APPROVAL_REQUEST:
                payload = event.payload
                self._request_locked(
                    payload.request_id, RequestKind.APPROVAL, payload.summary, payload.expires_at, True, payload.detail
                )
            elif kind is InteractionKind.USER_INPUT_REQUIRED:
                self._question_locked(event)
            elif kind is InteractionKind.USER_INPUT_ANSWERED:
                self._settle_locked(_ref(event.correlation_id), ResolvedBy.NATIVE, event.payload.answers)
            elif kind is InteractionKind.ATTENTION:
                self._attention_locked(event.payload.active)
            elif kind is InteractionKind.TASK_COMPLETED:
                refresh = self._finish_turn_locked({"status": "completed"}, "")
            elif kind is InteractionKind.TASK_FAILED:
                refresh = self._failed_locked(event)
        if refresh:
            self._refresh_catalog()

    def _on_resolved(self, request_id: str) -> None:
        with self._lock:
            if not self._closed.is_set():
                self._settle_locked(request_id, ResolvedBy.NATIVE)

    def _message_locked(self, event: InteractionEvent) -> None:
        payload = event.payload
        phase = _MESSAGE_PHASES.get(payload.phase)
        if phase is None:
            return
        self._ensure_turn_locked()
        meta: dict[str, Any] = {"message_ref": _ref(event.correlation_id), "role": payload.role.value, "phase": phase}
        if payload.role is AdapterMessageRole.USER and event.correlation_id == self._submitting:
            meta["command_id"] = event.correlation_id
        self._emit(FactKind.MESSAGE, meta, {"text": _clip(payload.text)})

    def _tool_locked(self, event: InteractionEvent) -> None:
        payload = event.payload
        # The adapter's tool name holds the command line or the paths, so it
        # travels as content and the label stays a fixed kind.
        text = payload.name if payload.phase is AdapterToolPhase.START else payload.text
        meta: dict[str, Any] = {
            "tool_ref": _ref(payload.tool_call_id),
            "label": getattr(_ITEM, "label", "tool"),
            "phase": payload.phase.value,
        }
        if payload.exit_code is not None:
            meta["exit_code"] = payload.exit_code
        if payload.status is not None:
            meta["status"] = payload.status.value
        if payload.stream is not None:
            meta["stream"] = payload.stream.value
        self._ensure_turn_locked()
        self._emit(FactKind.TOOL, meta, {"text": _clip(text)})

    def _question_locked(self, event: InteractionEvent) -> None:
        """A Codex question becomes an input request; one Codex does not wait
        on outlives its turn and stays answerable for an hour (ADR 0046)."""
        payload = event.payload
        try:
            questions = questions_field([dict(question) for question in payload.questions])
        except ProtocolViolation:
            questions = ()
        window = INPUT_WINDOW_MS if payload.blocking else ASYNC_INPUT_WINDOW_MS
        self._request_locked(
            event.correlation_id,
            RequestKind.INPUT,
            payload.prompt,
            self._clock() + window,
            bool(questions) and not any(question["secret"] for question in questions),
            "\n".join(question["title"] for question in questions),
            questions=questions,
            blocking=payload.blocking,
        )

    def _request_locked(
        self,
        ref: str,
        kind: RequestKind,
        summary: str,
        expires_at: int,
        answerable: bool,
        detail: str = "",
        *,
        questions: tuple[dict[str, Any], ...] = (),
        blocking: bool = True,
    ) -> None:
        ref = _ref(ref)
        if ref in self._requests or len(self._requests) >= MAX_OPEN_REQUESTS:
            return
        # A question Codex asks without waiting opens no turn of its own.
        if blocking:
            self._ensure_turn_locked()
        detail = _clip(detail, MAX_DETAIL_CHARS)
        request = OpenRequest(ref, kind, summary, expires_at, answerable, detail, blocking, questions)
        self._requests[ref] = request
        meta: dict[str, Any] = {
            "provider_ref": ref,
            "kind": kind.value,
            "summary": summary,
            "expires_at": expires_at,
            "answerable": answerable,
        }
        if not blocking:
            meta["blocking"] = False
        content: dict[str, Any] = {"detail": detail} if detail else {}
        if questions:
            content["questions"] = list(questions)
        self._emit(FactKind.REQUEST_OPENED, meta, content or None)

    def _attention_locked(self, active: bool) -> None:
        if active:
            self._ensure_turn_locked()
        if active != self._attention:
            self._attention = active
            self._emit(FactKind.STATUS, {"attention": active})

    def _failed_locked(self, event: InteractionEvent) -> bool:
        code, message = event.payload.error_code, event.payload.message
        correlation = event.correlation_id
        opened_by = self._turn.command_id if self._turn is not None else self._pending_turn
        if code in _TURN_ENDINGS:
            status, failure = _TURN_ENDINGS[code]
        elif correlation and correlation == opened_by:
            status, failure = "failed", _COMMAND_FAILURES.get(code, Failure.PROVIDER)
            self._ensure_turn_locked()
        else:
            # A refused steer or interrupt, or a thread notice: the turn, if
            # any, goes on, so this is a notice rather than a turn end.
            meta: dict[str, Any] = {"message_ref": f"notice-{uuid.uuid4().hex}", "role": "system", "phase": "complete"}
            if correlation:
                meta["command_id"] = _ref(correlation)
            self._emit(FactKind.MESSAGE, meta, {"text": f"{message} ({code})" if code else message})
            return False
        meta = {"status": status}
        if failure is not None:
            meta["failure"] = failure.value
        return self._finish_turn_locked(meta, message)

    # Helpers (called with the lock held) -------------------------------

    def _ensure_turn_locked(self) -> None:
        if self._turn is not None:
            return
        if self._pending_turn is not None:
            ref, self._pending_turn = self._pending_turn, None
            self._open_turn_locked(ref, ref)
        else:
            # A turn started in the TUI.
            self._open_turn_locked(f"codex-{uuid.uuid4().hex[:16]}", None)

    def _open_turn_locked(self, ref: str, command_id: str | None) -> None:
        self._turn = _Turn(ref, command_id)
        meta: dict[str, Any] = {"turn_ref": ref}
        if command_id:
            meta["command_id"] = command_id
        self._emit(FactKind.TURN_STARTED, meta)

    def _finish_turn_locked(self, meta: dict[str, Any], message: str) -> bool:
        if self._turn is not None:
            meta = {**meta, "turn_ref": self._turn.ref}
        self._emit(FactKind.TURN_TERMINAL, meta, {"message": message[:512]} if message else {})
        # The Bridge drops the turn's open requests and attention with it; a
        # question Codex does not wait on stays open.
        self._turn, self._attention = None, False
        self._requests = {ref: request for ref, request in self._requests.items() if not request.blocking}
        return self._refresh_enabled and time.monotonic() - self._catalog_at >= CATALOG_REFRESH_SECONDS

    def _settle_locked(self, ref: str, by: ResolvedBy, answers: dict[str, tuple[str, ...]] | None = None) -> None:
        if self._requests.pop(ref, None) is not None:
            # How the TUI answered is content, bounded as the protocol requires.
            bounded = {
                question_id: [_clip(item, MAX_ANSWER_CHARS) for item in items[:MAX_OPTIONS] if item.strip()]
                for question_id, items in (answers or {}).items()
                if question_id and len(question_id) <= MAX_IDENTITY_CHARS
            }
            bounded = {question_id: items for question_id, items in bounded.items() if items}
            self._emit(
                FactKind.REQUEST_RESOLVED, {"provider_ref": ref, "by": by.value}, {"answers": bounded} if bounded else None
            )

    # Background work ---------------------------------------------------

    def _watch(self) -> None:
        while not self._closed.wait(WATCH_SECONDS):
            attached = bool(self._adapter.attached_thread_id())
            with self._lock:
                inputs = [ref for ref, request in self._requests.items() if request.kind is RequestKind.INPUT and request.blocking]
            # Codex answered in the TUI, or the question expired.
            unanswered = self._adapter.pending_user_input_count() if inputs else 0
            became_ready = False
            with self._lock:
                if self._closed.is_set():
                    return
                if attached and not self._ready:
                    self._ready = became_ready = True
                    self._emit(FactKind.READY)
                if inputs and unanswered == 0:
                    for ref in inputs:
                        self._settle_locked(ref, ResolvedBy.NATIVE)
                now = self._clock()
                for ref in [ref for ref, request in self._requests.items() if request.expires_at <= now]:
                    del self._requests[ref]
            if became_ready:
                self._refresh_catalog()

    def _refresh_catalog(self, *, force: bool = False) -> None:
        with self._lock:
            if self._refreshing or self._closed.is_set() or not (self._refresh_enabled or force):
                return
            self._refreshing = True
        Thread(target=self._read_catalog, name="deskhelm-codex-catalog", daemon=True).start()

    def _read_catalog(self) -> None:
        try:
            catalog = self._adapter.session_catalog(self._key.agent_id, self._key.session_id, self._key.project_id)
        except Exception:
            catalog = None
        with self._lock:
            self._refreshing = False
            if self._closed.is_set() or not isinstance(catalog, dict) or not catalog.get("available"):
                return
            if len(json.dumps(catalog)) > MAX_CATALOG_BYTES:
                return
            self._catalog = catalog
            if catalog.get("models_notice") == "":
                self._catalog_at = time.monotonic()
            self._emit(FactKind.CATALOG, content={"catalog": catalog})
