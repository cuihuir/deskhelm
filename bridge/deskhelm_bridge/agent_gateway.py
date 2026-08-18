from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Event, RLock
from typing import Protocol

from .control import (
    ControlCommand,
    ControlKind,
    SubmitPromptPayload,
)
from .control_router import ControlRouter
from .interaction import (
    InteractionEvent,
    InteractionKind,
    InteractionPayload,
    TaskPayload,
)
from .session_registry import SessionKey


class AgentRunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    session: SessionKey
    prompt: str = field(repr=False)
    working_directory: Path
    provider_session_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if not isinstance(self.working_directory, Path):
            raise ValueError("working_directory must be a Path")
        if not self.working_directory.is_absolute():
            raise ValueError("working_directory must be absolute")
        if not isinstance(self.provider_session_id, str):
            raise ValueError("provider_session_id must be a string")


@dataclass(frozen=True, slots=True)
class AgentProviderEvent:
    kind: InteractionKind
    payload: InteractionPayload
    correlation_id: str

    def __post_init__(self) -> None:
        if self.kind not in {InteractionKind.MESSAGE, InteractionKind.TOOL}:
            raise ValueError("provider events must be message or tool events")
        if not isinstance(self.correlation_id, str) or not self.correlation_id.strip():
            raise ValueError("provider event correlation_id must not be empty")


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    status: AgentRunStatus
    message: str = ""
    error_code: str = ""
    provider_session_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, AgentRunStatus):
            raise ValueError("agent run status is invalid")
        for value, name in (
            (self.message, "message"),
            (self.error_code, "error_code"),
            (self.provider_session_id, "provider_session_id"),
        ):
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        if self.status is AgentRunStatus.COMPLETED and self.error_code:
            raise ValueError("completed run must not include error_code")


class AgentProvider(Protocol):
    source: str
    source_version: str

    def run(
        self,
        request: AgentRunRequest,
        emit: Callable[[AgentProviderEvent], None],
        cancel: Event,
    ) -> AgentRunResult: ...


@dataclass(slots=True)
class _GatewaySessionRecord:
    sequence: int = 0
    provider_session_id: str = ""


@dataclass(slots=True)
class AgentGateway:
    provider: AgentProvider
    publish_interaction: Callable[[InteractionEvent], None]
    working_directory: Path
    max_active_runs: int = 4
    max_session_records: int = 64
    _active: dict[SessionKey, Event] = field(default_factory=dict, init=False)
    _sessions: OrderedDict[SessionKey, _GatewaySessionRecord] = field(
        default_factory=OrderedDict, init=False
    )
    _unregister_handlers: list[Callable[[], None]] = field(
        default_factory=list, init=False
    )
    _closed: bool = field(default=False, init=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _executor: ThreadPoolExecutor = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.working_directory = self.working_directory.resolve()
        if self.max_active_runs < 1:
            raise ValueError("max_active_runs must be at least 1")
        if self.max_session_records < self.max_active_runs:
            raise ValueError(
                "max_session_records must be at least max_active_runs"
            )
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_active_runs,
            thread_name_prefix="deskhelm-agent-run",
        )

    def register_handlers(self, router: ControlRouter) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("agent gateway is closed")
            if self._unregister_handlers:
                raise RuntimeError("agent gateway handlers are already registered")
            unregister_prompt = router.register_handler(
                ControlKind.SUBMIT_PROMPT, self.submit_prompt
            )
            try:
                unregister_interrupt = router.register_handler(
                    ControlKind.INTERRUPT, self.interrupt
                )
            except BaseException:
                unregister_prompt()
                raise
            self._unregister_handlers = [
                unregister_prompt,
                unregister_interrupt,
            ]

    def submit_prompt(self, command: ControlCommand) -> None:
        payload = command.payload
        if not isinstance(payload, SubmitPromptPayload):
            raise ValueError("submit_prompt command has an invalid payload")
        session = self._session(command)
        with self._lock:
            if self._closed:
                raise RuntimeError("agent gateway is closed")
            if session in self._active:
                raise RuntimeError("target session already has an active run")
            if len(self._active) >= self.max_active_runs:
                raise RuntimeError("agent gateway active run capacity is exhausted")
            record = self._ensure_session_record(session)
            cancel = Event()
            self._active[session] = cancel
            request = AgentRunRequest(
                session=session,
                prompt=payload.text,
                working_directory=self.working_directory,
                provider_session_id=record.provider_session_id,
            )
            try:
                self._executor.submit(self._run, request, cancel)
            except BaseException:
                del self._active[session]
                raise

    def interrupt(self, command: ControlCommand) -> None:
        session = self._session(command)
        with self._lock:
            cancel = self._active.get(session)
            if cancel is None:
                raise RuntimeError("target session has no active run")
            cancel.set()

    def active_run_count(self) -> int:
        with self._lock:
            return len(self._active)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            unregister_handlers = tuple(self._unregister_handlers)
            self._unregister_handlers.clear()
            for cancel in self._active.values():
                cancel.set()
        for unregister in unregister_handlers:
            unregister()
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _run(self, request: AgentRunRequest, cancel: Event) -> None:
        try:
            try:
                result = self.provider.run(
                    request,
                    lambda event: self._publish_provider_event(
                        request.session, event
                    ),
                    cancel,
                )
                if not isinstance(result, AgentRunResult):
                    raise TypeError("agent provider returned an invalid result")
            except Exception:
                result = AgentRunResult(
                    status=AgentRunStatus.FAILED,
                    message="Agent provider failed",
                    error_code="provider_error",
                    provider_session_id=request.provider_session_id,
                )

            if result.provider_session_id:
                with self._lock:
                    record = self._sessions.get(request.session)
                    if record is not None:
                        record.provider_session_id = result.provider_session_id

            if result.status is AgentRunStatus.COMPLETED:
                self._publish_terminal(
                    request.session,
                    InteractionKind.TASK_COMPLETED,
                    result.message,
                    "",
                )
            else:
                error_code = result.error_code or result.status.value
                message = result.message or {
                    AgentRunStatus.CANCELLED: "Agent run cancelled",
                    AgentRunStatus.TIMED_OUT: "Agent run timed out",
                    AgentRunStatus.FAILED: "Agent run failed",
                }[result.status]
                self._publish_terminal(
                    request.session,
                    InteractionKind.TASK_FAILED,
                    message,
                    error_code,
                )
        finally:
            with self._lock:
                self._active.pop(request.session, None)

    def _publish_provider_event(
        self, session: SessionKey, event: AgentProviderEvent
    ) -> None:
        self.publish_interaction(
            InteractionEvent(
                kind=event.kind,
                agent_id=session.agent_id,
                session_id=session.session_id,
                project_id=session.project_id,
                source=self.provider.source,
                source_version=self.provider.source_version,
                sequence=self._next_sequence(session),
                correlation_id=event.correlation_id,
                payload=event.payload,
            )
        )

    def _publish_terminal(
        self,
        session: SessionKey,
        kind: InteractionKind,
        message: str,
        error_code: str,
    ) -> None:
        self.publish_interaction(
            InteractionEvent(
                kind=kind,
                agent_id=session.agent_id,
                session_id=session.session_id,
                project_id=session.project_id,
                source=self.provider.source,
                source_version=self.provider.source_version,
                sequence=self._next_sequence(session),
                payload=TaskPayload(message=message, error_code=error_code),
            )
        )

    def _next_sequence(self, session: SessionKey) -> int:
        with self._lock:
            record = self._sessions.get(session)
            if record is None:
                raise RuntimeError("agent gateway session record is missing")
            sequence = record.sequence
            record.sequence += 1
            self._sessions.move_to_end(session)
            return sequence

    def _ensure_session_record(self, session: SessionKey) -> _GatewaySessionRecord:
        record = self._sessions.get(session)
        if record is not None:
            self._sessions.move_to_end(session)
            return record
        while len(self._sessions) >= self.max_session_records:
            evicted = next(
                (
                    candidate
                    for candidate in self._sessions
                    if candidate not in self._active
                ),
                None,
            )
            if evicted is None:
                raise RuntimeError("agent gateway session capacity is exhausted")
            del self._sessions[evicted]
        record = _GatewaySessionRecord()
        self._sessions[session] = record
        return record

    @staticmethod
    def _session(command: ControlCommand) -> SessionKey:
        return SessionKey(
            agent_id=command.agent_id,
            session_id=command.session_id,
            project_id=command.project_id,
        )
