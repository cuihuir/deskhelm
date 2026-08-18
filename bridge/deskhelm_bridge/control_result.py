from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any

from .event import PROTOCOL_VERSION, ProtocolError


CONTROL_RESULT_MESSAGE_TYPE = "control_result"


class ControlResultStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ControlResultCode(StrEnum):
    FOCUSED = "focused"
    DISPATCHED = "dispatched"
    EXPIRED = "expired"
    ISSUER_MISMATCH = "issuer_mismatch"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    IDEMPOTENCY_CAPACITY = "idempotency_capacity"
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_INACTIVE = "target_inactive"
    HANDLER_UNAVAILABLE = "handler_unavailable"
    DISPATCH_FAILED = "dispatch_failed"
    APPROVAL_NOT_FOUND = "approval_not_found"
    APPROVAL_TARGET_MISMATCH = "approval_target_mismatch"
    APPROVAL_SUMMARY_MISMATCH = "approval_summary_mismatch"
    APPROVAL_EXPIRY_MISMATCH = "approval_expiry_mismatch"
    APPROVAL_ALREADY_DECIDED = "approval_already_decided"


_ACCEPTED_CODES = {
    ControlResultCode.FOCUSED,
    ControlResultCode.DISPATCHED,
}


@dataclass(frozen=True, slots=True)
class ControlResult:
    command_id: str
    status: ControlResultStatus
    code: ControlResultCode
    processed_at: int
    duplicate: bool = False
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = CONTROL_RESULT_MESSAGE_TYPE

    def __post_init__(self) -> None:
        if not isinstance(self.protocol_version, int) or isinstance(
            self.protocol_version, bool
        ):
            raise ProtocolError("protocol_version must be an integer")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(
                f"unsupported protocol_version {self.protocol_version}; expected {PROTOCOL_VERSION}"
            )
        if self.message_type != CONTROL_RESULT_MESSAGE_TYPE:
            raise ProtocolError(f"unsupported message_type {self.message_type}")
        if not isinstance(self.command_id, str) or not self.command_id.strip():
            raise ProtocolError("command_id must not be empty")
        if not isinstance(self.status, ControlResultStatus):
            raise ProtocolError("control result status is invalid")
        if not isinstance(self.code, ControlResultCode):
            raise ProtocolError("control result code is invalid")
        if not isinstance(self.processed_at, int) or isinstance(self.processed_at, bool):
            raise ProtocolError("processed_at must be an integer")
        if self.processed_at <= 0:
            raise ProtocolError("processed_at must be greater than zero")
        if not isinstance(self.duplicate, bool):
            raise ProtocolError("duplicate must be a boolean")
        if (self.code in _ACCEPTED_CODES) != (
            self.status is ControlResultStatus.ACCEPTED
        ):
            raise ProtocolError("control result status does not match code")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "command_id": self.command_id,
            "status": self.status.value,
            "code": self.code.value,
            "processed_at": self.processed_at,
            "duplicate": self.duplicate,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ControlResult:
        try:
            return cls(
                protocol_version=_required_integer(value, "protocol_version"),
                message_type=_required_string(value, "message_type"),
                command_id=_required_string(value, "command_id"),
                status=ControlResultStatus(_required_string(value, "status")),
                code=ControlResultCode(_required_string(value, "code")),
                processed_at=_required_integer(value, "processed_at"),
                duplicate=_required_boolean(value, "duplicate"),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ProtocolError):
                raise
            raise ProtocolError(str(error)) from error

    @classmethod
    def from_json(cls, line: str) -> ControlResult:
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ProtocolError(f"invalid JSON: {error.msg}") from error
        if not isinstance(value, dict):
            raise ProtocolError("control result must be a JSON object")
        return cls.from_dict(value)


def _required_string(value: dict[str, Any], field_name: str) -> str:
    field_value = value.get(field_name)
    if not isinstance(field_value, str) or not field_value.strip():
        raise ProtocolError(f"{field_name} must not be empty")
    return field_value


def _required_integer(value: dict[str, Any], field_name: str) -> int:
    field_value = value.get(field_name)
    if not isinstance(field_value, int) or isinstance(field_value, bool):
        raise ProtocolError(f"{field_name} must be an integer")
    return field_value


def _required_boolean(value: dict[str, Any], field_name: str) -> bool:
    field_value = value.get(field_name)
    if not isinstance(field_value, bool):
        raise ProtocolError(f"{field_name} must be a boolean")
    return field_value
