from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from threading import RLock
import time

from .control import ApprovalDecisionPayload, ControlCommand, ControlKind
from .control_result import (
    ControlResult,
    ControlResultCode,
    ControlResultStatus,
)
from .interaction import ApprovalRequestPayload, InteractionEvent, InteractionKind
from .session_registry import SessionKey, SessionLifecycleState, SessionRegistry


ControlHandler = Callable[[ControlCommand], None]
IdempotencyScope = tuple[str, str]


@dataclass(frozen=True, slots=True)
class PendingApproval:
    request_id: str
    session: SessionKey
    summary: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class _IdempotencyEntry:
    fingerprint: str
    result: ControlResult
    retained_until: int


@dataclass(slots=True)
class ControlRouter:
    session_registry: SessionRegistry
    max_idempotency_entries: int = 1024
    idempotency_retention_ms: int = 300_000
    max_approval_records: int = 1024
    _handlers: dict[ControlKind, ControlHandler] = field(
        default_factory=dict, init=False, repr=False
    )
    _idempotency: OrderedDict[IdempotencyScope, _IdempotencyEntry] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _pending_approvals: dict[str, PendingApproval] = field(
        default_factory=dict, init=False, repr=False
    )
    _decided_approvals: dict[str, int] = field(
        default_factory=dict, init=False, repr=False
    )
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_idempotency_entries < 1:
            raise ValueError("max_idempotency_entries must be at least 1")
        if self.idempotency_retention_ms < 1:
            raise ValueError("idempotency_retention_ms must be at least 1")
        if self.max_approval_records < 1:
            raise ValueError("max_approval_records must be at least 1")

    def register_handler(
        self, kind: ControlKind, handler: ControlHandler
    ) -> Callable[[], None]:
        if not isinstance(kind, ControlKind):
            raise ValueError("control kind is invalid")
        if not callable(handler):
            raise ValueError("control handler must be callable")
        if kind is ControlKind.FOCUS:
            raise ValueError("focus is handled internally")
        with self._lock:
            if kind in self._handlers:
                raise ValueError(f"handler for {kind.value} is already registered")
            self._handlers[kind] = handler

        def unregister() -> None:
            with self._lock:
                if self._handlers.get(kind) is handler:
                    del self._handlers[kind]

        return unregister

    def observe_interaction(self, event: InteractionEvent) -> None:
        if event.kind is not InteractionKind.APPROVAL_REQUEST:
            return
        payload = event.payload
        if not isinstance(payload, ApprovalRequestPayload):
            return
        pending = PendingApproval(
            request_id=payload.request_id,
            session=SessionKey(
                agent_id=event.agent_id,
                session_id=event.session_id,
                project_id=event.project_id,
            ),
            summary=payload.summary,
            expires_at=payload.expires_at,
        )
        with self._lock:
            self._purge(event.occurred_at)
            if payload.request_id in self._decided_approvals:
                return
            existing = self._pending_approvals.get(payload.request_id)
            if existing == pending:
                return
            if existing is None and (
                len(self._pending_approvals) + len(self._decided_approvals)
                >= self.max_approval_records
            ):
                return
            if existing is None:
                self._pending_approvals[payload.request_id] = pending

    def route(
        self,
        command: ControlCommand,
        *,
        expected_issued_by: str | None = None,
        now_ms: int | None = None,
    ) -> ControlResult:
        timestamp = self._timestamp(now_ms)
        if expected_issued_by is not None and command.issued_by != expected_issued_by:
            return self._rejected(
                command, ControlResultCode.ISSUER_MISMATCH, timestamp
            )
        if command.is_expired(now_ms=timestamp):
            return self._rejected(command, ControlResultCode.EXPIRED, timestamp)

        scope = (command.issued_by, command.idempotency_key)
        fingerprint = command.to_json()
        with self._lock:
            self._purge(timestamp)
            existing = self._idempotency.get(scope)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    return self._rejected(
                        command, ControlResultCode.IDEMPOTENCY_CONFLICT, timestamp
                    )
                return replace(existing.result, duplicate=True, processed_at=timestamp)

            session = SessionKey(
                agent_id=command.agent_id,
                session_id=command.session_id,
                project_id=command.project_id,
            )
            record = self.session_registry.record_for(session)
            if record is None:
                return self._rejected(
                    command, ControlResultCode.TARGET_NOT_FOUND, timestamp
                )
            if record.state is not SessionLifecycleState.ACTIVE:
                return self._rejected(
                    command, ControlResultCode.TARGET_INACTIVE, timestamp
                )

            approval_error = self._validate_approval(command, session, timestamp)
            if approval_error is not None:
                return self._rejected(command, approval_error, timestamp)

            handler = None
            if command.kind is not ControlKind.FOCUS:
                handler = self._handlers.get(command.kind)
                if handler is None:
                    return self._rejected(
                        command, ControlResultCode.HANDLER_UNAVAILABLE, timestamp
                    )
            if len(self._idempotency) >= self.max_idempotency_entries:
                return self._rejected(
                    command, ControlResultCode.IDEMPOTENCY_CAPACITY, timestamp
                )

            try:
                if command.kind is ControlKind.FOCUS:
                    self.session_registry.focus(session)
                    result = self._accepted(
                        command, ControlResultCode.FOCUSED, timestamp
                    )
                else:
                    handler(command)
                    result = self._accepted(
                        command, ControlResultCode.DISPATCHED, timestamp
                    )
            except Exception:
                result = self._rejected(
                    command, ControlResultCode.DISPATCH_FAILED, timestamp
                )

            if command.kind in {
                ControlKind.APPROVE,
                ControlKind.REJECT,
            }:
                payload = command.payload
                if isinstance(payload, ApprovalDecisionPayload):
                    self._pending_approvals.pop(payload.request_id, None)
                    self._decided_approvals[payload.request_id] = (
                        payload.request_expires_at
                    )

            self._idempotency[scope] = _IdempotencyEntry(
                fingerprint=fingerprint,
                result=result,
                retained_until=max(
                    command.expires_at,
                    timestamp + self.idempotency_retention_ms,
                ),
            )
            return result

    def idempotency_size(self) -> int:
        with self._lock:
            return len(self._idempotency)

    def pending_approval_count(self) -> int:
        with self._lock:
            return len(self._pending_approvals)

    def _validate_approval(
        self, command: ControlCommand, session: SessionKey, timestamp: int
    ) -> ControlResultCode | None:
        if command.kind not in {ControlKind.APPROVE, ControlKind.REJECT}:
            return None
        payload = command.payload
        if not isinstance(payload, ApprovalDecisionPayload):
            return ControlResultCode.APPROVAL_NOT_FOUND
        if payload.request_id in self._decided_approvals:
            return ControlResultCode.APPROVAL_ALREADY_DECIDED
        pending = self._pending_approvals.get(payload.request_id)
        if pending is None or timestamp >= pending.expires_at:
            return ControlResultCode.APPROVAL_NOT_FOUND
        if pending.session != session:
            return ControlResultCode.APPROVAL_TARGET_MISMATCH
        if pending.summary != payload.summary:
            return ControlResultCode.APPROVAL_SUMMARY_MISMATCH
        if pending.expires_at != payload.request_expires_at:
            return ControlResultCode.APPROVAL_EXPIRY_MISMATCH
        return None

    def _purge(self, timestamp: int) -> None:
        expired_scopes = [
            scope
            for scope, entry in self._idempotency.items()
            if entry.retained_until <= timestamp
        ]
        for scope in expired_scopes:
            del self._idempotency[scope]
        expired_requests = [
            request_id
            for request_id, pending in self._pending_approvals.items()
            if pending.expires_at <= timestamp
        ]
        for request_id in expired_requests:
            del self._pending_approvals[request_id]
        expired_decisions = [
            request_id
            for request_id, expires_at in self._decided_approvals.items()
            if expires_at <= timestamp
        ]
        for request_id in expired_decisions:
            del self._decided_approvals[request_id]

    @staticmethod
    def _accepted(
        command: ControlCommand, code: ControlResultCode, timestamp: int
    ) -> ControlResult:
        return ControlResult(
            command_id=command.command_id,
            status=ControlResultStatus.ACCEPTED,
            code=code,
            processed_at=timestamp,
        )

    @staticmethod
    def _rejected(
        command: ControlCommand, code: ControlResultCode, timestamp: int
    ) -> ControlResult:
        return ControlResult(
            command_id=command.command_id,
            status=ControlResultStatus.REJECTED,
            code=code,
            processed_at=timestamp,
        )

    @staticmethod
    def _timestamp(now_ms: int | None) -> int:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        if (
            not isinstance(timestamp, int)
            or isinstance(timestamp, bool)
            or timestamp <= 0
        ):
            raise ValueError("now_ms must be a positive integer")
        return timestamp
