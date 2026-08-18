from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any

from .event import PROTOCOL_VERSION, ProtocolError


ADAPTER_SESSION_MESSAGE_TYPE = "adapter_session"
ADAPTER_SESSION_RESULT_MESSAGE_TYPE = "adapter_session_result"


class AdapterSessionAction(StrEnum):
    REGISTER = "register"
    DISCONNECT = "disconnect"
    RELEASE = "release"


class AdapterCapability(StrEnum):
    STATE_EVENTS = "state_events"
    INTERACTION_EVENTS = "interaction_events"
    TOOL_EVENTS = "tool_events"
    APPROVAL_REQUESTS = "approval_requests"
    SESSION_RESUME = "session_resume"
    SUBMIT_PROMPT = "submit_prompt"
    INTERRUPT = "interrupt"
    APPROVAL_DECISIONS = "approval_decisions"


@dataclass(frozen=True, slots=True)
class AdapterSessionEvent:
    action: AdapterSessionAction
    adapter_id: str
    adapter_version: str
    runtime_name: str
    runtime_version: str
    agent_id: str
    session_id: str
    project_id: str
    capabilities: tuple[AdapterCapability, ...]
    occurred_at: int
    preferred_slot: int | None = None
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = ADAPTER_SESSION_MESSAGE_TYPE

    def __post_init__(self) -> None:
        _validate_header(
            self.protocol_version, self.message_type, ADAPTER_SESSION_MESSAGE_TYPE
        )
        if not isinstance(self.action, AdapterSessionAction):
            raise ProtocolError("adapter session action is invalid")
        for field_name in (
            "adapter_id",
            "adapter_version",
            "runtime_name",
            "runtime_version",
            "agent_id",
            "session_id",
            "project_id",
        ):
            _validate_non_empty_string(getattr(self, field_name), field_name)
        if not isinstance(self.capabilities, tuple) or not self.capabilities:
            raise ProtocolError("adapter capabilities must be a non-empty array")
        if not all(
            isinstance(capability, AdapterCapability)
            for capability in self.capabilities
        ):
            raise ProtocolError("adapter capability is invalid")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ProtocolError("adapter capabilities must not contain duplicates")
        _validate_positive_integer(self.occurred_at, "occurred_at")
        if self.preferred_slot is not None:
            _validate_non_negative_integer(self.preferred_slot, "preferred_slot")
            if self.action is not AdapterSessionAction.REGISTER:
                raise ProtocolError("preferred_slot is allowed only for register")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "action": self.action.value,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "runtime_name": self.runtime_name,
            "runtime_version": self.runtime_version,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "capabilities": [capability.value for capability in self.capabilities],
            "occurred_at": self.occurred_at,
            "preferred_slot": self.preferred_slot,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AdapterSessionEvent:
        try:
            capabilities = _required_list(value, "capabilities")
            return cls(
                protocol_version=_required_integer(value, "protocol_version"),
                message_type=_required_string(value, "message_type"),
                action=AdapterSessionAction(_required_string(value, "action")),
                adapter_id=_required_string(value, "adapter_id"),
                adapter_version=_required_string(value, "adapter_version"),
                runtime_name=_required_string(value, "runtime_name"),
                runtime_version=_required_string(value, "runtime_version"),
                agent_id=_required_string(value, "agent_id"),
                session_id=_required_string(value, "session_id"),
                project_id=_required_string(value, "project_id"),
                capabilities=tuple(
                    AdapterCapability(capability) for capability in capabilities
                ),
                occurred_at=_required_positive_integer(value, "occurred_at"),
                preferred_slot=_optional_integer(value, "preferred_slot"),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ProtocolError):
                raise
            raise ProtocolError(str(error)) from error

    @classmethod
    def from_json(cls, line: str) -> AdapterSessionEvent:
        return cls.from_dict(_decode_object(line, "adapter session event"))


@dataclass(frozen=True, slots=True)
class AdapterSessionResult:
    action: AdapterSessionAction
    agent_id: str
    session_id: str
    project_id: str
    occurred_at: int
    slot: int | None
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = ADAPTER_SESSION_RESULT_MESSAGE_TYPE

    def __post_init__(self) -> None:
        _validate_header(
            self.protocol_version,
            self.message_type,
            ADAPTER_SESSION_RESULT_MESSAGE_TYPE,
        )
        if not isinstance(self.action, AdapterSessionAction):
            raise ProtocolError("adapter session action is invalid")
        _validate_non_empty_string(self.agent_id, "agent_id")
        _validate_non_empty_string(self.session_id, "session_id")
        _validate_non_empty_string(self.project_id, "project_id")
        _validate_positive_integer(self.occurred_at, "occurred_at")
        if self.slot is not None:
            _validate_non_negative_integer(self.slot, "slot")
        if self.action is not AdapterSessionAction.RELEASE and self.slot is None:
            raise ProtocolError("register and disconnect results require slot")
        if self.action is AdapterSessionAction.RELEASE and self.slot is not None:
            raise ProtocolError("release result slot must be null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "action": self.action.value,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "occurred_at": self.occurred_at,
            "slot": self.slot,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AdapterSessionResult:
        try:
            return cls(
                protocol_version=_required_integer(value, "protocol_version"),
                message_type=_required_string(value, "message_type"),
                action=AdapterSessionAction(_required_string(value, "action")),
                agent_id=_required_string(value, "agent_id"),
                session_id=_required_string(value, "session_id"),
                project_id=_required_string(value, "project_id"),
                occurred_at=_required_positive_integer(value, "occurred_at"),
                slot=_optional_integer(value, "slot"),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ProtocolError):
                raise
            raise ProtocolError(str(error)) from error

    @classmethod
    def from_json(cls, line: str) -> AdapterSessionResult:
        return cls.from_dict(_decode_object(line, "adapter session result"))


def _decode_object(line: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise ProtocolError(f"invalid JSON: {error.msg}") from error
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be a JSON object")
    return value


def _validate_header(
    protocol_version: object, message_type: object, expected_message_type: str
) -> None:
    _validate_integer(protocol_version, "protocol_version")
    if protocol_version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol_version {protocol_version}; expected {PROTOCOL_VERSION}"
        )
    if message_type != expected_message_type:
        raise ProtocolError(f"unsupported message_type {message_type}")


def _required_list(value: dict[str, Any], field_name: str) -> list[str]:
    field_value = value.get(field_name)
    if not isinstance(field_value, list) or not all(
        isinstance(item, str) for item in field_value
    ):
        raise ProtocolError(f"{field_name} must be an array of strings")
    return field_value


def _required_string(value: dict[str, Any], field_name: str) -> str:
    field_value = value.get(field_name)
    _validate_non_empty_string(field_value, field_name)
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


def _validate_non_negative_integer(value: object, field_name: str) -> None:
    _validate_integer(value, field_name)
    if value < 0:
        raise ProtocolError(f"{field_name} must be zero or greater")
