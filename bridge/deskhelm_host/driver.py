"""The contract every vendor driver implements (ADR 0043 §4.5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from deskhelm_protocol import (
    CapabilityMatrix,
    EffectKind,
    EffectOutcome,
    FactKind,
    OpenRequest,
    RuntimeIdentity,
)


class FactSink(Protocol):
    def __call__(
        self,
        kind: FactKind,
        meta: dict[str, Any] | None = None,
        content: dict[str, Any] | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionState:
    """What a host reports when it reopens its session on a fresh Bridge."""

    ready: bool = False
    turn_ref: str | None = None
    attention: bool = False
    open_requests: tuple[OpenRequest, ...] = ()


class DriverSession(Protocol):
    """One vendor session.

    Rules: every terminal path settles the turn's open requests; requests over
    the limit are declined or left to the native UI, never dropped silently;
    approvals pick the vendor's one-time option; ``meta`` stays content-free.
    """

    runtime: RuntimeIdentity
    capabilities: CapabilityMatrix

    def start(self, emit: FactSink) -> None: ...

    def perform(self, kind: EffectKind, payload: dict[str, Any]) -> EffectOutcome:
        """Queue the effect and return at once. Raise only when the outcome is unknown."""
        ...

    def state(self) -> SessionState: ...

    def wait(self) -> int: ...

    def close(self) -> None: ...
