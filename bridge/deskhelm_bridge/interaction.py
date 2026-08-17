from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import time
from typing import Any
import uuid

from .event import PROTOCOL_VERSION, ProtocolError


INTERACTION_MESSAGE_TYPE = "interaction_event"


class InteractionKind(StrEnum):
    MESSAGE = "message"
    TOOL = "tool"
    APPROVAL_REQUEST = "approval_request"
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


@dataclass(frozen=True, slots=True)
class MessagePayload:
    role: MessageRole
    phase: MessagePhase
    text: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.role, MessageRole):
            raise ProtocolError("message role is invalid")
        if not isinstance(self.phase, MessagePhase):
            raise ProtocolError("message phase is invalid")
        if not isinstance(self.text, str):
            raise ProtocolError("message text must be a string")
        if self.phase is MessagePhase.DELTA and not self.text:
            raise ProtocolError("message delta text must not be empty")

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

    def __post_init__(self) -> None:
        if not isinstance(self.phase, ToolPhase):
            raise ProtocolError("tool phase is invalid")
        if self.stream is not None and not isinstance(self.stream, ToolStream):
            raise ProtocolError("tool stream is invalid")
        _validate_non_empty_string(self.tool_call_id, "tool_call_id")
        _validate_non_empty_string(self.name, "tool name")
        if not isinstance(self.text, str):
            raise ProtocolError("tool text must be a string")
        if self.phase is ToolPhase.OUTPUT:
            if not self.text:
                raise ProtocolError("tool output text must not be empty")
            if self.stream is None:
                raise ProtocolError("tool output stream is required")
        elif self.stream is not None:
            raise ProtocolError("tool stream is only valid for output events")
        if self.exit_code is not None:
            _validate_integer(self.exit_code, "exit_code")
            if self.phase is not ToolPhase.COMPLETE:
                raise ProtocolError("exit_code is only valid for complete tool events")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "phase": self.phase.value,
            "text": self.text,
            "stream": None if self.stream is None else self.stream.value,
            "exit_code": self.exit_code,
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
        return {
            "request_id": self.request_id,
            "summary": self.summary,
            "expires_at": self.expires_at,
        }


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
            raise ProtocolError("task message must be a string")
        if not isinstance(self.error_code, str):
            raise ProtocolError("task error_code must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {"message": self.message, "error_code": self.error_code}


InteractionPayload = (
    MessagePayload
    | ToolPayload
    | ApprovalRequestPayload
    | UserInputPayload
    | TaskPayload
)


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
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = INTERACTION_MESSAGE_TYPE

    def __post_init__(self) -> None:
        if not isinstance(self.kind, InteractionKind):
            raise ProtocolError("interaction kind is invalid")
        _validate_non_empty_string(self.event_id, "event_id")
        _validate_non_empty_string(self.agent_id, "agent_id")
        _validate_non_empty_string(self.session_id, "session_id")
        _validate_non_empty_string(self.project_id, "project_id")
        _validate_non_empty_string(self.source, "source")
        _validate_non_empty_string(self.source_version, "source_version")
        _validate_integer(self.sequence, "sequence")
        if self.sequence < 0:
            raise ProtocolError("sequence must be zero or greater")
        _validate_integer(self.protocol_version, "protocol_version")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(
                f"unsupported protocol_version {self.protocol_version}; expected {PROTOCOL_VERSION}"
            )
        if self.message_type != INTERACTION_MESSAGE_TYPE:
            raise ProtocolError(f"unsupported message_type {self.message_type}")
        _validate_integer(self.occurred_at, "occurred_at")
        if self.occurred_at == 0:
            object.__setattr__(self, "occurred_at", int(time.time() * 1000))
        _validate_positive_integer(self.occurred_at, "occurred_at")

        expected_payload = {
            InteractionKind.MESSAGE: MessagePayload,
            InteractionKind.TOOL: ToolPayload,
            InteractionKind.APPROVAL_REQUEST: ApprovalRequestPayload,
            InteractionKind.USER_INPUT_REQUIRED: UserInputPayload,
            InteractionKind.TASK_COMPLETED: TaskPayload,
            InteractionKind.TASK_FAILED: TaskPayload,
        }[self.kind]
        if not isinstance(self.payload, expected_payload):
            raise ProtocolError(
                f"payload for {self.kind.value} must be {expected_payload.__name__}"
            )

        if self.kind in {
            InteractionKind.MESSAGE,
            InteractionKind.TOOL,
            InteractionKind.APPROVAL_REQUEST,
        }:
            _validate_non_empty_string(self.correlation_id, "correlation_id")
        elif not isinstance(self.correlation_id, str):
            raise ProtocolError("correlation_id must be a string")

        if isinstance(self.payload, ApprovalRequestPayload):
            if self.correlation_id != self.payload.request_id:
                raise ProtocolError("approval correlation_id must match request_id")
            if self.payload.expires_at <= self.occurred_at:
                raise ProtocolError("approval expires_at must be after occurred_at")
        if isinstance(self.payload, ToolPayload):
            if self.correlation_id != self.payload.tool_call_id:
                raise ProtocolError("tool correlation_id must match tool_call_id")
        if self.kind is InteractionKind.TASK_FAILED:
            if not self.payload.message:
                raise ProtocolError("failed task message must not be empty")
        if self.kind is InteractionKind.TASK_COMPLETED:
            if self.payload.error_code:
                raise ProtocolError("completed task must not include error_code")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
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

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> InteractionEvent:
        try:
            kind = InteractionKind(_required_string(value, "kind"))
            payload_value = _required_mapping(value, "payload")
            payload = _payload_from_dict(kind, payload_value)
            return cls(
                protocol_version=_required_integer(value, "protocol_version"),
                message_type=_required_string(value, "message_type"),
                event_id=_required_string(value, "event_id"),
                kind=kind,
                agent_id=_required_string(value, "agent_id"),
                session_id=_required_string(value, "session_id"),
                project_id=_required_string(value, "project_id"),
                source=_required_string(value, "source"),
                source_version=_required_string(value, "source_version"),
                sequence=_required_integer(value, "sequence"),
                occurred_at=_required_positive_integer(value, "occurred_at"),
                correlation_id=_optional_string(value, "correlation_id"),
                payload=payload,
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ProtocolError):
                raise
            raise ProtocolError(str(error)) from error

    @classmethod
    def from_json(cls, line: str) -> InteractionEvent:
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ProtocolError(f"invalid JSON: {error.msg}") from error
        if not isinstance(value, dict):
            raise ProtocolError("interaction event must be a JSON object")
        return cls.from_dict(value)


def _payload_from_dict(
    kind: InteractionKind, value: dict[str, Any]
) -> InteractionPayload:
    if kind is InteractionKind.MESSAGE:
        return MessagePayload(
            role=MessageRole(_required_string(value, "role")),
            phase=MessagePhase(_required_string(value, "phase")),
            text=_optional_string(value, "text"),
        )
    if kind is InteractionKind.TOOL:
        stream_value = value.get("stream")
        return ToolPayload(
            tool_call_id=_required_string(value, "tool_call_id"),
            name=_required_string(value, "name"),
            phase=ToolPhase(_required_string(value, "phase")),
            text=_optional_string(value, "text"),
            stream=None if stream_value is None else ToolStream(stream_value),
            exit_code=_optional_integer(value, "exit_code"),
        )
    if kind is InteractionKind.APPROVAL_REQUEST:
        return ApprovalRequestPayload(
            request_id=_required_string(value, "request_id"),
            summary=_required_string(value, "summary"),
            expires_at=_required_integer(value, "expires_at"),
        )
    if kind is InteractionKind.USER_INPUT_REQUIRED:
        return UserInputPayload(prompt=_required_string(value, "prompt"))
    return TaskPayload(
        message=_optional_string(value, "message"),
        error_code=_optional_string(value, "error_code"),
    )


def _required_mapping(value: dict[str, Any], field_name: str) -> dict[str, Any]:
    field_value = value.get(field_name)
    if not isinstance(field_value, dict):
        raise ProtocolError(f"{field_name} must be an object")
    return field_value


def _required_string(value: dict[str, Any], field_name: str) -> str:
    field_value = value.get(field_name)
    _validate_non_empty_string(field_value, field_name)
    return field_value


def _optional_string(value: dict[str, Any], field_name: str) -> str:
    field_value = value.get(field_name, "")
    if not isinstance(field_value, str):
        raise ProtocolError(f"{field_name} must be a string")
    return field_value


def _required_integer(value: dict[str, Any], field_name: str) -> int:
    field_value = value.get(field_name)
    _validate_integer(field_value, field_name)
    return field_value


def _required_positive_integer(value: dict[str, Any], field_name: str) -> int:
    field_value = value.get(field_name)
    _validate_positive_integer(field_value, field_name)
    return field_value


def _optional_integer(value: dict[str, Any], field_name: str) -> int | None:
    field_value = value.get(field_name)
    if field_value is None:
        return None
    _validate_integer(field_value, field_name)
    return field_value


def _validate_non_empty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{field_name} must not be empty")


def _validate_integer(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProtocolError(f"{field_name} must be an integer")


def _validate_positive_integer(value: object, field_name: str) -> None:
    _validate_integer(value, field_name)
    if value <= 0:
        raise ProtocolError(f"{field_name} must be greater than zero")
