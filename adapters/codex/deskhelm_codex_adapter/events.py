"""What the app-server adapter publishes, before the driver makes facts of it.

These are the v1 interaction shapes the adapter was built and tested with,
kept inside the adapter when v1 left (ADR 0043 phase 4) so its behavior did
not change. ``driver.CodexSession`` turns them into protocol v2 facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import time
from typing import Any
import uuid


class InteractionKind(StrEnum):
    MESSAGE = "message"
    TOOL = "tool"
    APPROVAL_REQUEST = "approval_request"
    ATTENTION = "attention"
    USER_INPUT_REQUIRED = "user_input_required"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessagePhase(StrEnum):
    START = "start"
    DELTA = "delta"
    COMPLETE = "complete"


class ToolPhase(StrEnum):
    START = "start"
    OUTPUT = "output"
    COMPLETE = "complete"


class ToolStream(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"


class ToolStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    DECLINED = "declined"


class AttentionReason(StrEnum):
    APPROVAL = "approval"


@dataclass(frozen=True, slots=True)
class MessagePayload:
    role: MessageRole
    phase: MessagePhase
    text: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.role, MessageRole):
            raise ValueError("message role is invalid")
        if not isinstance(self.phase, MessagePhase):
            raise ValueError("message phase is invalid")
        if not isinstance(self.text, str):
            raise ValueError("message text must be a string")
        if self.phase is MessagePhase.DELTA and not self.text:
            raise ValueError("message delta text must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role.value, "phase": self.phase.value, "text": self.text}


@dataclass(frozen=True, slots=True)
class ToolPayload:
    tool_call_id: str
    name: str
    phase: ToolPhase
    text: str = ""
    stream: ToolStream | None = None
    exit_code: int | None = None
    status: ToolStatus | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.phase, ToolPhase):
            raise ValueError("tool phase is invalid")
        if (self.phase is ToolPhase.COMPLETE) != isinstance(self.status, ToolStatus):
            raise ValueError("a complete tool event, and only one, has a status")
        if self.stream is not None and not isinstance(self.stream, ToolStream):
            raise ValueError("tool stream is invalid")
        _validate_non_empty_string(self.tool_call_id, "tool_call_id")
        _validate_non_empty_string(self.name, "tool name")
        if not isinstance(self.text, str):
            raise ValueError("tool text must be a string")
        if self.phase is ToolPhase.OUTPUT:
            if not self.text:
                raise ValueError("tool output text must not be empty")
            if self.stream is None:
                raise ValueError("tool output stream is required")
        elif self.stream is not None:
            raise ValueError("tool stream is only valid for output events")
        if self.exit_code is not None:
            _validate_integer(self.exit_code, "exit_code")
            if self.phase is not ToolPhase.COMPLETE:
                raise ValueError("exit_code is only valid for complete tool events")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "phase": self.phase.value,
            "text": self.text,
            "stream": None if self.stream is None else self.stream.value,
            "exit_code": self.exit_code,
            "status": None if self.status is None else self.status.value,
        }


@dataclass(frozen=True, slots=True)
class ApprovalRequestPayload:
    request_id: str
    summary: str
    expires_at: int

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.request_id, "request_id")
        _validate_non_empty_string(self.summary, "approval summary")
        _validate_positive_integer(self.expires_at, "expires_at")

    def to_dict(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "summary": self.summary, "expires_at": self.expires_at}


@dataclass(frozen=True, slots=True)
class AttentionPayload:
    reason: AttentionReason
    active: bool

    def __post_init__(self) -> None:
        if not isinstance(self.reason, AttentionReason):
            raise ValueError("attention reason is invalid")
        if not isinstance(self.active, bool):
            raise ValueError("attention active must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason.value, "active": self.active}


@dataclass(frozen=True, slots=True)
class UserInputPayload:
    prompt: str

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.prompt, "input prompt")

    def to_dict(self) -> dict[str, Any]:
        return {"prompt": self.prompt}


@dataclass(frozen=True, slots=True)
class TaskPayload:
    message: str = ""
    error_code: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.message, str):
            raise ValueError("task message must be a string")
        if not isinstance(self.error_code, str):
            raise ValueError("task error_code must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {"message": self.message, "error_code": self.error_code}


InteractionPayload = (
    MessagePayload | ToolPayload | ApprovalRequestPayload | AttentionPayload | UserInputPayload | TaskPayload
)

_EXPECTED_PAYLOAD = {
    InteractionKind.MESSAGE: MessagePayload,
    InteractionKind.TOOL: ToolPayload,
    InteractionKind.APPROVAL_REQUEST: ApprovalRequestPayload,
    InteractionKind.ATTENTION: AttentionPayload,
    InteractionKind.USER_INPUT_REQUIRED: UserInputPayload,
    InteractionKind.TASK_COMPLETED: TaskPayload,
    InteractionKind.TASK_FAILED: TaskPayload,
}


@dataclass(frozen=True, slots=True)
class InteractionEvent:
    kind: InteractionKind
    agent_id: str
    session_id: str
    project_id: str
    source: str
    source_version: str
    sequence: int
    payload: InteractionPayload
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: int = 0
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, InteractionKind):
            raise ValueError("interaction kind is invalid")
        for value, name in (
            (self.event_id, "event_id"),
            (self.agent_id, "agent_id"),
            (self.session_id, "session_id"),
            (self.project_id, "project_id"),
            (self.source, "source"),
            (self.source_version, "source_version"),
        ):
            _validate_non_empty_string(value, name)
        _validate_integer(self.sequence, "sequence")
        if self.sequence < 0:
            raise ValueError("sequence must be zero or greater")
        _validate_integer(self.occurred_at, "occurred_at")
        if self.occurred_at == 0:
            object.__setattr__(self, "occurred_at", int(time.time() * 1000))
        _validate_positive_integer(self.occurred_at, "occurred_at")
        expected = _EXPECTED_PAYLOAD[self.kind]
        if not isinstance(self.payload, expected):
            raise ValueError(f"payload for {self.kind.value} must be {expected.__name__}")
        if self.kind in {InteractionKind.MESSAGE, InteractionKind.TOOL, InteractionKind.APPROVAL_REQUEST}:
            _validate_non_empty_string(self.correlation_id, "correlation_id")
        elif not isinstance(self.correlation_id, str):
            raise ValueError("correlation_id must be a string")
        if isinstance(self.payload, ApprovalRequestPayload):
            if self.correlation_id != self.payload.request_id:
                raise ValueError("approval correlation_id must match request_id")
            if self.payload.expires_at <= self.occurred_at:
                raise ValueError("approval expires_at must be after occurred_at")
        if isinstance(self.payload, ToolPayload) and self.correlation_id != self.payload.tool_call_id:
            raise ValueError("tool correlation_id must match tool_call_id")
        if self.kind is InteractionKind.TASK_FAILED and not self.payload.message:
            raise ValueError("failed task message must not be empty")
        if self.kind is InteractionKind.TASK_COMPLETED and self.payload.error_code:
            raise ValueError("completed task must not include error_code")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "source": self.source,
            "source_version": self.source_version,
            "sequence": self.sequence,
            "occurred_at": self.occurred_at,
            "correlation_id": self.correlation_id,
            "payload": self.payload.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)


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
