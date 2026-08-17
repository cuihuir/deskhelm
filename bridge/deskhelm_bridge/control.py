from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import time
from typing import Any
import uuid

from .event import PROTOCOL_VERSION, ProtocolError


CONTROL_COMMAND_MESSAGE_TYPE = "control_command"


class ControlKind(StrEnum):
    FOCUS = "focus"
    SUBMIT_PROMPT = "submit_prompt"
    INTERRUPT = "interrupt"
    APPROVE = "approve"
    REJECT = "reject"
    SPEAK = "speak"
    STOP_SPEAKING = "stop_speaking"


class SpeechPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class FocusPayload:
    def to_dict(self) -> dict[str, Any]:
        return {}


@dataclass(frozen=True, slots=True)
class SubmitPromptPayload:
    text: str

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.text, "prompt text")

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text}


@dataclass(frozen=True, slots=True)
class InterruptPayload:
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str):
            raise ProtocolError("interrupt reason must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason}


@dataclass(frozen=True, slots=True)
class ApprovalDecisionPayload:
    request_id: str
    summary: str
    request_expires_at: int

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.request_id, "request_id")
        _validate_non_empty_string(self.summary, "approval summary")
        _validate_positive_integer(self.request_expires_at, "request_expires_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "summary": self.summary,
            "request_expires_at": self.request_expires_at,
        }


@dataclass(frozen=True, slots=True)
class SpeakPayload:
    text: str
    priority: SpeechPriority = SpeechPriority.NORMAL
    interruptible: bool = True

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.text, "speech text")
        if not isinstance(self.priority, SpeechPriority):
            raise ProtocolError("speech priority is invalid")
        if not isinstance(self.interruptible, bool):
            raise ProtocolError("interruptible must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "priority": self.priority.value,
            "interruptible": self.interruptible,
        }


@dataclass(frozen=True, slots=True)
class StopSpeakingPayload:
    speech_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.speech_id, str):
            raise ProtocolError("speech_id must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {"speech_id": self.speech_id}


ControlPayload = (
    FocusPayload
    | SubmitPromptPayload
    | InterruptPayload
    | ApprovalDecisionPayload
    | SpeakPayload
    | StopSpeakingPayload
)


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
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = CONTROL_COMMAND_MESSAGE_TYPE

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ControlKind):
            raise ProtocolError("control kind is invalid")
        _validate_non_empty_string(self.command_id, "command_id")
        _validate_non_empty_string(self.agent_id, "agent_id")
        _validate_non_empty_string(self.session_id, "session_id")
        _validate_non_empty_string(self.project_id, "project_id")
        _validate_non_empty_string(self.issued_by, "issued_by")
        _validate_non_empty_string(self.idempotency_key, "idempotency_key")
        _validate_integer(self.protocol_version, "protocol_version")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(
                f"unsupported protocol_version {self.protocol_version}; expected {PROTOCOL_VERSION}"
            )
        if self.message_type != CONTROL_COMMAND_MESSAGE_TYPE:
            raise ProtocolError(f"unsupported message_type {self.message_type}")
        _validate_integer(self.issued_at, "issued_at")
        if self.issued_at == 0:
            object.__setattr__(self, "issued_at", int(time.time() * 1000))
        _validate_positive_integer(self.issued_at, "issued_at")
        _validate_positive_integer(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ProtocolError("expires_at must be after issued_at")

        expected_payload = {
            ControlKind.FOCUS: FocusPayload,
            ControlKind.SUBMIT_PROMPT: SubmitPromptPayload,
            ControlKind.INTERRUPT: InterruptPayload,
            ControlKind.APPROVE: ApprovalDecisionPayload,
            ControlKind.REJECT: ApprovalDecisionPayload,
            ControlKind.SPEAK: SpeakPayload,
            ControlKind.STOP_SPEAKING: StopSpeakingPayload,
        }[self.kind]
        if not isinstance(self.payload, expected_payload):
            raise ProtocolError(
                f"payload for {self.kind.value} must be {expected_payload.__name__}"
            )

        if isinstance(self.payload, ApprovalDecisionPayload):
            if self.expires_at != self.payload.request_expires_at:
                raise ProtocolError(
                    "approval expires_at must match request_expires_at"
                )

    def is_expired(self, *, now_ms: int | None = None) -> bool:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        _validate_positive_integer(timestamp, "now_ms")
        return timestamp >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "command_id": self.command_id,
            "kind": self.kind.value,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "issued_by": self.issued_by,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ControlCommand:
        try:
            kind = ControlKind(_required_string(value, "kind"))
            payload_value = _required_mapping(value, "payload")
            return cls(
                protocol_version=_required_integer(value, "protocol_version"),
                message_type=_required_string(value, "message_type"),
                command_id=_required_string(value, "command_id"),
                kind=kind,
                agent_id=_required_string(value, "agent_id"),
                session_id=_required_string(value, "session_id"),
                project_id=_required_string(value, "project_id"),
                issued_by=_required_string(value, "issued_by"),
                issued_at=_required_positive_integer(value, "issued_at"),
                expires_at=_required_positive_integer(value, "expires_at"),
                idempotency_key=_required_string(value, "idempotency_key"),
                payload=_payload_from_dict(kind, payload_value),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ProtocolError):
                raise
            raise ProtocolError(str(error)) from error

    @classmethod
    def from_json(cls, line: str) -> ControlCommand:
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ProtocolError(f"invalid JSON: {error.msg}") from error
        if not isinstance(value, dict):
            raise ProtocolError("control command must be a JSON object")
        return cls.from_dict(value)


def _payload_from_dict(kind: ControlKind, value: dict[str, Any]) -> ControlPayload:
    if kind is ControlKind.FOCUS:
        return FocusPayload()
    if kind is ControlKind.SUBMIT_PROMPT:
        return SubmitPromptPayload(text=_required_string(value, "text"))
    if kind is ControlKind.INTERRUPT:
        return InterruptPayload(reason=_optional_string(value, "reason"))
    if kind in {ControlKind.APPROVE, ControlKind.REJECT}:
        return ApprovalDecisionPayload(
            request_id=_required_string(value, "request_id"),
            summary=_required_string(value, "summary"),
            request_expires_at=_required_positive_integer(
                value, "request_expires_at"
            ),
        )
    if kind is ControlKind.SPEAK:
        return SpeakPayload(
            text=_required_string(value, "text"),
            priority=SpeechPriority(_required_string(value, "priority")),
            interruptible=_required_boolean(value, "interruptible"),
        )
    return StopSpeakingPayload(speech_id=_optional_string(value, "speech_id"))


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


def _required_boolean(value: dict[str, Any], field_name: str) -> bool:
    field_value = value.get(field_name)
    if not isinstance(field_value, bool):
        raise ProtocolError(f"{field_name} must be a boolean")
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
