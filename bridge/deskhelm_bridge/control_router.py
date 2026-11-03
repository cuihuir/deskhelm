from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from threading import Condition, RLock
import time
from typing import Any

from .control import (
    ApprovalDecisionPayload,
    ControlCommand,
    ControlKind,
    ControlUndelivered,
)
from .control_result import (
    ControlResult,
    ControlResultCode,
    ControlResultStatus,
)
from .interaction import ApprovalRequestPayload, InteractionEvent, InteractionKind
from .session_registry import SessionKey, SessionLifecycleState, SessionRegistry


ControlHandler = Callable[[ControlCommand], None]
CatalogProvider = Callable[[str, str, str], Mapping[str, Any]]
IdempotencyScope = tuple[str, str]
_APPROVAL_KINDS = frozenset({ControlKind.APPROVE, ControlKind.REJECT})


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
class _InFlight:
    fingerprint: str
    result: ControlResult | None = None


@dataclass(slots=True)
class _ApprovalReservation:
    pending: PendingApproval
    resolved: bool = False


@dataclass(slots=True)
class ControlRouter:
    session_registry: SessionRegistry
    max_idempotency_entries: int = 1024
    idempotency_retention_ms: int = 300_000
    max_approval_records: int = 1024
    duplicate_wait_ms: int = 5_000
    _handlers: dict[ControlKind, ControlHandler] = field(
        default_factory=dict, init=False, repr=False
    )
    _idempotency: OrderedDict[IdempotencyScope, _IdempotencyEntry] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _in_flight: dict[IdempotencyScope, _InFlight] = field(
        default_factory=dict, init=False, repr=False
    )
    _pending_approvals: dict[str, PendingApproval] = field(
        default_factory=dict, init=False, repr=False
    )
    _reserved_approvals: dict[str, _ApprovalReservation] = field(
        default_factory=dict, init=False, repr=False
    )
    _decided_approvals: dict[str, int] = field(
        default_factory=dict, init=False, repr=False
    )
    _catalog_provider: CatalogProvider | None = field(
        default=None, init=False, repr=False
    )
    # Handlers run outside this lock. The condition lets a duplicate wait for
    # the in-flight dispatch it repeats.
    _lock: Condition = field(
        default_factory=lambda: Condition(RLock()), init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.max_idempotency_entries < 1:
            raise ValueError("max_idempotency_entries must be at least 1")
        if self.idempotency_retention_ms < 1:
            raise ValueError("idempotency_retention_ms must be at least 1")
        if self.max_approval_records < 1:
            raise ValueError("max_approval_records must be at least 1")
        if self.duplicate_wait_ms < 1:
            raise ValueError("duplicate_wait_ms must be at least 1")

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

    def register_catalog_provider(
        self, provider: CatalogProvider
    ) -> Callable[[], None]:
        if not callable(provider):
            raise ValueError("catalog provider must be callable")
        with self._lock:
            if self._catalog_provider is not None:
                raise ValueError("catalog provider is already registered")
            self._catalog_provider = provider

        def unregister() -> None:
            with self._lock:
                if self._catalog_provider is provider:
                    self._catalog_provider = None

        return unregister

    def catalog_provider(self) -> CatalogProvider | None:
        with self._lock:
            return self._catalog_provider

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
            if (
                payload.request_id in self._decided_approvals
                or payload.request_id in self._reserved_approvals
            ):
                return
            existing = self._pending_approvals.get(payload.request_id)
            if existing == pending:
                return
            if existing is None and (
                len(self._pending_approvals)
                + len(self._reserved_approvals)
                + len(self._decided_approvals)
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
            in_flight = self._in_flight.get(scope)
            if in_flight is not None:
                if in_flight.fingerprint != fingerprint:
                    return self._rejected(
                        command, ControlResultCode.IDEMPOTENCY_CONFLICT, timestamp
                    )
                return self._await_in_flight(command, in_flight, timestamp)

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
            if (
                command.kind is ControlKind.PRESS_PTT
                and self.session_registry.focused_session() != session
            ):
                return self._rejected(
                    command, ControlResultCode.TARGET_NOT_FOCUSED, timestamp
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
            if (
                len(self._idempotency) + len(self._in_flight)
                >= self.max_idempotency_entries
            ):
                return self._rejected(
                    command, ControlResultCode.IDEMPOTENCY_CAPACITY, timestamp
                )

            if handler is None:
                try:
                    self.session_registry.focus(session)
                    result = self._accepted(
                        command, ControlResultCode.FOCUSED, timestamp
                    )
                except Exception:
                    result = self._rejected(
                        command, ControlResultCode.DISPATCH_FAILED, timestamp
                    )
                self._remember(scope, fingerprint, command, result, timestamp)
                return result

            in_flight = _InFlight(fingerprint)
            self._in_flight[scope] = in_flight
            reserved = self._reserve_approval(command)

        # A handler may publish events, and the publish path takes this lock
        # to record approvals, so the handler must run without it.
        code = ControlResultCode.DISPATCH_FAILED
        delivered = True
        try:
            handler(command)
            code = ControlResultCode.DISPATCHED
        except ControlUndelivered:
            code = ControlResultCode.HANDLER_UNAVAILABLE
            delivered = False
        except Exception:
            # The handler may have acted before failing, so count it delivered.
            code = ControlResultCode.DISPATCH_FAILED
        finally:
            result = self._finish(
                scope, in_flight, command, code, delivered, reserved, timestamp
            )
        return result

    def idempotency_size(self) -> int:
        with self._lock:
            return len(self._idempotency)

    def pending_approval_count(self) -> int:
        with self._lock:
            return len(self._pending_approvals)

    def pending_approvals_for(
        self,
        session: SessionKey,
        *,
        now_ms: int | None = None,
    ) -> tuple[PendingApproval, ...]:
        timestamp = self._timestamp(now_ms)
        with self._lock:
            self._purge(timestamp)
            return tuple(
                sorted(
                    (
                        pending
                        for pending in self._pending_approvals.values()
                        if pending.session == session
                    ),
                    key=lambda pending: (
                        pending.expires_at,
                        pending.request_id,
                    ),
                )
            )

    def resolve_approval(
        self,
        request_id: str,
        *,
        now_ms: int | None = None,
    ) -> bool:
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id must not be empty")
        timestamp = self._timestamp(now_ms)
        with self._lock:
            self._purge(timestamp)
            pending = self._pending_approvals.pop(request_id, None)
            if pending is not None:
                self._decided_approvals[request_id] = pending.expires_at
                return True
            reservation = self._reserved_approvals.get(request_id)
            if reservation is None or reservation.resolved:
                return False
            reservation.resolved = True
            return True

    def _await_in_flight(
        self, command: ControlCommand, in_flight: _InFlight, timestamp: int
    ) -> ControlResult:
        deadline = time.monotonic() + self.duplicate_wait_ms / 1000
        while in_flight.result is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # The first dispatch is still running, so its outcome is unknown.
                return replace(
                    self._rejected(
                        command, ControlResultCode.DISPATCH_FAILED, timestamp
                    ),
                    duplicate=True,
                )
            self._lock.wait(remaining)
        return replace(in_flight.result, duplicate=True, processed_at=timestamp)

    def _finish(
        self,
        scope: IdempotencyScope,
        in_flight: _InFlight,
        command: ControlCommand,
        code: ControlResultCode,
        delivered: bool,
        reserved: str | None,
        timestamp: int,
    ) -> ControlResult:
        if code is ControlResultCode.DISPATCHED:
            result = self._accepted(command, code, timestamp)
        else:
            result = self._rejected(command, code, timestamp)
        with self._lock:
            if reserved is not None:
                self._settle_approval(reserved, delivered=delivered)
            self._in_flight.pop(scope, None)
            if delivered:
                self._remember(
                    scope, in_flight.fingerprint, command, result, timestamp
                )
            in_flight.result = result
            self._lock.notify_all()
        return result

    def _remember(
        self,
        scope: IdempotencyScope,
        fingerprint: str,
        command: ControlCommand,
        result: ControlResult,
        timestamp: int,
    ) -> None:
        self._idempotency[scope] = _IdempotencyEntry(
            fingerprint=fingerprint,
            result=result,
            retained_until=max(
                command.expires_at,
                timestamp + self.idempotency_retention_ms,
            ),
        )

    def _reserve_approval(self, command: ControlCommand) -> str | None:
        if command.kind not in _APPROVAL_KINDS:
            return None
        payload = command.payload
        if not isinstance(payload, ApprovalDecisionPayload):
            return None
        pending = self._pending_approvals.pop(payload.request_id, None)
        if pending is None:
            return None
        self._reserved_approvals[payload.request_id] = _ApprovalReservation(pending)
        return payload.request_id

    def _settle_approval(self, request_id: str, *, delivered: bool) -> None:
        reservation = self._reserved_approvals.pop(request_id, None)
        if reservation is None:
            return
        if delivered or reservation.resolved:
            self._decided_approvals[request_id] = reservation.pending.expires_at
        else:
            self._pending_approvals[request_id] = reservation.pending

    def _validate_approval(
        self, command: ControlCommand, session: SessionKey, timestamp: int
    ) -> ControlResultCode | None:
        if command.kind not in _APPROVAL_KINDS:
            return None
        payload = command.payload
        if not isinstance(payload, ApprovalDecisionPayload):
            return ControlResultCode.APPROVAL_NOT_FOUND
        if (
            payload.request_id in self._decided_approvals
            or payload.request_id in self._reserved_approvals
        ):
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
