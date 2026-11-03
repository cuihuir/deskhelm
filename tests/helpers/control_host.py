"""A stand-in host for the Codex app-server adapter tests.

It keeps what those tests observe of the removed v1 control router: handlers
by kind, approvals recorded from published approval requests, an approval
consumed once its decision may have reached Codex but kept when the handler
proves it never did (``ControlUndelivered``), and decided approvals refused.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
import time
from typing import Any

from deskhelm_codex_adapter.controls import ApprovalDecisionPayload, ControlCommand, ControlKind, ControlUndelivered
from deskhelm_codex_adapter.events import ApprovalRequestPayload, InteractionEvent, InteractionKind


class ControlResultCode(StrEnum):
    DISPATCHED = "dispatched"
    DISPATCH_FAILED = "dispatch_failed"
    HANDLER_UNAVAILABLE = "handler_unavailable"
    APPROVAL_NOT_FOUND = "approval_not_found"
    APPROVAL_ALREADY_DECIDED = "approval_already_decided"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class RouteResult:
    code: ControlResultCode


@dataclass(slots=True)
class _Reservation:
    expires_at: int
    resolved: bool = False


class FakeControlHost:
    def __init__(self) -> None:
        self._lock = RLock()
        self._handlers: dict[ControlKind, Callable[[ControlCommand], None]] = {}
        self._catalog: Callable[..., dict[str, Any]] | None = None
        self._pending: dict[str, int] = {}
        self._reserved: dict[str, _Reservation] = {}
        self._decided: set[str] = set()

    # ControlHost -------------------------------------------------------

    def register_handler(self, kind: ControlKind, handler: Callable[[ControlCommand], None]) -> Callable[[], None]:
        with self._lock:
            self._handlers[kind] = handler

        def unregister() -> None:
            with self._lock:
                if self._handlers.get(kind) is handler:
                    del self._handlers[kind]

        return unregister

    def register_catalog_provider(self, provider: Callable[..., dict[str, Any]]) -> Callable[[], None]:
        with self._lock:
            self._catalog = provider

        def unregister() -> None:
            with self._lock:
                if self._catalog is provider:
                    self._catalog = None

        return unregister

    def resolve_approval(self, request_id: str) -> None:
        with self._lock:
            if self._pending.pop(request_id, None) is not None:
                self._decided.add(request_id)
            elif request_id in self._reserved:
                self._reserved[request_id].resolved = True

    # Test surface ------------------------------------------------------

    def observe_interaction(self, event: InteractionEvent) -> None:
        if event.kind is not InteractionKind.APPROVAL_REQUEST or not isinstance(event.payload, ApprovalRequestPayload):
            return
        with self._lock:
            request_id = event.payload.request_id
            if request_id not in self._decided and request_id not in self._reserved:
                self._pending.setdefault(request_id, event.payload.expires_at)

    def pending_approval_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def route(self, command: ControlCommand, *, now_ms: int | None = None) -> RouteResult:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if now >= command.expires_at:
            return RouteResult(ControlResultCode.EXPIRED)
        request_id = ""
        with self._lock:
            if isinstance(command.payload, ApprovalDecisionPayload):
                request_id = command.payload.request_id
                if request_id in self._decided or request_id in self._reserved:
                    return RouteResult(ControlResultCode.APPROVAL_ALREADY_DECIDED)
                expires_at = self._pending.get(request_id)
                if expires_at is None or now >= expires_at:
                    return RouteResult(ControlResultCode.APPROVAL_NOT_FOUND)
            handler = self._handlers.get(command.kind)
            if handler is None:
                return RouteResult(ControlResultCode.HANDLER_UNAVAILABLE)
            if request_id:
                self._reserved[request_id] = _Reservation(self._pending.pop(request_id))
        # The adapter publishes from inside some handlers, so call it unlocked.
        delivered = True
        try:
            handler(command)
            code = ControlResultCode.DISPATCHED
        except ControlUndelivered:
            code, delivered = ControlResultCode.HANDLER_UNAVAILABLE, False
        except Exception:
            # The handler may have acted before failing, so it counts as delivered.
            code = ControlResultCode.DISPATCH_FAILED
        if request_id:
            with self._lock:
                reservation = self._reserved.pop(request_id)
                if delivered or reservation.resolved:
                    self._decided.add(request_id)
                else:
                    self._pending[request_id] = reservation.expires_at
        return RouteResult(code)
