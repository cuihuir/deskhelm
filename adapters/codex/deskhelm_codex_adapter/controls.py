"""What the app-server adapter accepts, before the driver makes it of effects.

These are the v1 control shapes the adapter was built and tested with, kept
inside the adapter when v1 left (ADR 0043 phase 4). ``driver.CodexSession``
turns protocol v2 effects into these commands and is the adapter's
``ControlHost``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
import time
from typing import Any, Protocol
import uuid


class ControlUndelivered(RuntimeError):
    """Raised by a handler that knows its command never reached the runtime."""


class ControlKind(StrEnum):
    SUBMIT_PROMPT = "submit_prompt"
    INTERRUPT = "interrupt"
    APPROVE = "approve"
    REJECT = "reject"
    ANSWER_INPUT = "answer_input"
    CONFIGURE_SESSION = "configure_session"


@dataclass(frozen=True, slots=True)
class SessionKey:
    agent_id: str
    session_id: str = ""
    project_id: str = ""

    def __post_init__(self) -> None:
        if not self.agent_id.strip():
            raise ValueError("agent_id must not be empty")


@dataclass(frozen=True, slots=True)
class SubmitPromptPayload:
    text: str

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.text, "prompt text")


@dataclass(frozen=True, slots=True)
class InterruptPayload:
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str):
            raise ValueError("interrupt reason must be a string")


@dataclass(frozen=True, slots=True)
class ApprovalDecisionPayload:
    request_id: str
    summary: str
    request_expires_at: int

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.request_id, "request_id")
        _validate_non_empty_string(self.summary, "approval summary")
        _validate_positive_integer(self.request_expires_at, "request_expires_at")


@dataclass(frozen=True, slots=True)
class AnswerInputPayload:
    """Answers to one Codex question request, by question id."""

    request_id: str
    answers: dict[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.request_id, "request_id")
        if not isinstance(self.answers, dict) or not self.answers:
            raise ValueError("answers must name at least one question")
        for question_id, items in self.answers.items():
            _validate_non_empty_string(question_id, "question id")
            if not isinstance(items, tuple) or not items or not all(isinstance(item, str) and item.strip() for item in items):
                raise ValueError("each question takes non-empty answers")


@dataclass(frozen=True, slots=True)
class ConfigureSessionPayload:
    model_set: bool
    model: str
    effort_set: bool
    reasoning_effort: str

    def __post_init__(self) -> None:
        if not isinstance(self.model_set, bool) or not isinstance(self.effort_set, bool):
            raise ValueError("configure_session flags must be boolean")
        if not self.model_set and not self.effort_set:
            raise ValueError("configure_session requires model or reasoning_effort")
        _validate_config_value(self.model, "model", 128)
        _validate_config_value(self.reasoning_effort, "reasoning_effort", 64)


ControlPayload = (
    SubmitPromptPayload | InterruptPayload | ApprovalDecisionPayload | AnswerInputPayload | ConfigureSessionPayload
)

_EXPECTED_PAYLOAD = {
    ControlKind.SUBMIT_PROMPT: SubmitPromptPayload,
    ControlKind.INTERRUPT: InterruptPayload,
    ControlKind.APPROVE: ApprovalDecisionPayload,
    ControlKind.REJECT: ApprovalDecisionPayload,
    ControlKind.ANSWER_INPUT: AnswerInputPayload,
    ControlKind.CONFIGURE_SESSION: ConfigureSessionPayload,
}


@dataclass(frozen=True, slots=True)
class ControlCommand:
    kind: ControlKind
    agent_id: str
    session_id: str
    project_id: str
    issued_by: str
    expires_at: int
    idempotency_key: str
    payload: ControlPayload
    command_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    issued_at: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ControlKind):
            raise ValueError("control kind is invalid")
        for value, name in (
            (self.command_id, "command_id"),
            (self.agent_id, "agent_id"),
            (self.session_id, "session_id"),
            (self.project_id, "project_id"),
            (self.issued_by, "issued_by"),
            (self.idempotency_key, "idempotency_key"),
        ):
            _validate_non_empty_string(value, name)
        _validate_integer(self.issued_at, "issued_at")
        if self.issued_at == 0:
            object.__setattr__(self, "issued_at", int(time.time() * 1000))
        _validate_positive_integer(self.issued_at, "issued_at")
        _validate_positive_integer(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        expected = _EXPECTED_PAYLOAD[self.kind]
        if not isinstance(self.payload, expected):
            raise ValueError(f"payload for {self.kind.value} must be {expected.__name__}")
        if isinstance(self.payload, ApprovalDecisionPayload) and self.expires_at != self.payload.request_expires_at:
            raise ValueError("approval expires_at must match request_expires_at")


class ControlHost(Protocol):
    """The adapter's host: the v2 driver, or a test double."""

    def register_handler(self, kind: ControlKind, handler: Callable[[ControlCommand], None]) -> Callable[[], None]: ...

    def register_catalog_provider(self, provider: Callable[[str, str, str], dict[str, Any]]) -> Callable[[], None]: ...

    def resolve_approval(self, request_id: str) -> None: ...


def _validate_config_value(value: object, field_name: str, limit: int) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if len(value) > limit:
        raise ValueError(f"{field_name} is too long")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must be a single token")


def _validate_non_empty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _validate_integer(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")


def _validate_positive_integer(value: object, field_name: str) -> None:
    _validate_integer(value, field_name)
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
