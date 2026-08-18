from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any, BinaryIO

from .event import PROTOCOL_VERSION, ProtocolError


MAX_FRAME_BYTES = 1024 * 1024
CLIENT_HELLO_MESSAGE_TYPE = "client_hello"
SERVER_HELLO_MESSAGE_TYPE = "server_hello"
PROTOCOL_ERROR_MESSAGE_TYPE = "protocol_error"
AGENT_EVENT_MESSAGE_TYPE = "agent_event"
AGENT_EVENT_V1_CAPABILITY = "agent_event_v1"
INTERACTION_EVENT_V1_CAPABILITY = "interaction_event_v1"
INTERACTION_SUBSCRIPTION_V1_CAPABILITY = "interaction_subscription_v1"
STATE_SUBSCRIPTION_V1_CAPABILITY = "state_subscription_v1"


class ClientRole(StrEnum):
    PUBLISHER = "publisher"
    SUBSCRIBER = "subscriber"
    CONTROLLER = "controller"


@dataclass(frozen=True, slots=True)
class ClientHello:
    client_id: str
    role: ClientRole
    supported_versions: tuple[int, ...]
    capabilities: tuple[str, ...]
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = CLIENT_HELLO_MESSAGE_TYPE

    def __post_init__(self) -> None:
        _validate_protocol_header(
            self.protocol_version, self.message_type, CLIENT_HELLO_MESSAGE_TYPE
        )
        _validate_non_empty_string(self.client_id, "client_id")
        if not isinstance(self.role, ClientRole):
            raise ProtocolError("client role is invalid")
        _validate_versions(self.supported_versions)
        _validate_capabilities(self.capabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "client_id": self.client_id,
            "role": self.role.value,
            "supported_versions": list(self.supported_versions),
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ClientHello:
        try:
            versions = _required_list(value, "supported_versions")
            capabilities = _required_list(value, "capabilities")
            return cls(
                protocol_version=_required_integer(value, "protocol_version"),
                message_type=_required_string(value, "message_type"),
                client_id=_required_string(value, "client_id"),
                role=ClientRole(_required_string(value, "role")),
                supported_versions=tuple(versions),
                capabilities=tuple(capabilities),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ProtocolError):
                raise
            raise ProtocolError(str(error)) from error


@dataclass(frozen=True, slots=True)
class ServerHello:
    selected_version: int
    accepted_capabilities: tuple[str, ...]
    stream_id: str
    max_frame_bytes: int
    max_connections: int
    max_subscribers: int | None = None
    subscriber_queue_frames: int | None = None
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = SERVER_HELLO_MESSAGE_TYPE

    def __post_init__(self) -> None:
        _validate_protocol_header(
            self.protocol_version, self.message_type, SERVER_HELLO_MESSAGE_TYPE
        )
        _validate_positive_integer(self.selected_version, "selected_version")
        _validate_capabilities(self.accepted_capabilities)
        _validate_non_empty_string(self.stream_id, "stream_id")
        _validate_positive_integer(self.max_frame_bytes, "max_frame_bytes")
        _validate_positive_integer(self.max_connections, "max_connections")
        if (self.max_subscribers is None) != (self.subscriber_queue_frames is None):
            raise ProtocolError("subscriber limits must be provided together")
        if self.max_subscribers is not None:
            _validate_non_negative_integer(self.max_subscribers, "max_subscribers")
            if self.max_subscribers >= self.max_connections:
                raise ProtocolError("max_subscribers must be less than max_connections")
            _validate_positive_integer(
                self.subscriber_queue_frames, "subscriber_queue_frames"
            )

    def to_dict(self) -> dict[str, Any]:
        limits = {
            "max_frame_bytes": self.max_frame_bytes,
            "max_connections": self.max_connections,
        }
        if self.max_subscribers is not None:
            limits["max_subscribers"] = self.max_subscribers
            limits["subscriber_queue_frames"] = self.subscriber_queue_frames
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "selected_version": self.selected_version,
            "accepted_capabilities": list(self.accepted_capabilities),
            "stream_id": self.stream_id,
            "limits": limits,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ServerHello:
        limits = _required_mapping(value, "limits")
        capabilities = _required_list(value, "accepted_capabilities")
        return cls(
            protocol_version=_required_integer(value, "protocol_version"),
            message_type=_required_string(value, "message_type"),
            selected_version=_required_integer(value, "selected_version"),
            accepted_capabilities=tuple(capabilities),
            stream_id=_required_string(value, "stream_id"),
            max_frame_bytes=_required_integer(limits, "max_frame_bytes"),
            max_connections=_required_integer(limits, "max_connections"),
            max_subscribers=_optional_integer(limits, "max_subscribers"),
            subscriber_queue_frames=_optional_integer(
                limits, "subscriber_queue_frames"
            ),
        )


@dataclass(frozen=True, slots=True)
class ProtocolErrorFrame:
    code: str
    message: str
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = PROTOCOL_ERROR_MESSAGE_TYPE

    def __post_init__(self) -> None:
        _validate_protocol_header(
            self.protocol_version, self.message_type, PROTOCOL_ERROR_MESSAGE_TYPE
        )
        _validate_non_empty_string(self.code, "error code")
        _validate_non_empty_string(self.message, "error message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "code": self.code,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProtocolErrorFrame:
        return cls(
            protocol_version=_required_integer(value, "protocol_version"),
            message_type=_required_string(value, "message_type"),
            code=_required_string(value, "code"),
            message=_required_string(value, "message"),
        )


def read_frame(reader: BinaryIO) -> bytes | None:
    frame = reader.readline(MAX_FRAME_BYTES + 2)
    if not frame:
        return None
    if not frame.endswith(b"\n"):
        if len(frame) > MAX_FRAME_BYTES:
            raise ProtocolError(
                f"frame exceeds maximum size of {MAX_FRAME_BYTES} bytes"
            )
        raise ProtocolError("frame must end with a newline")
    frame = frame[:-1]
    if frame.endswith(b"\r"):
        frame = frame[:-1]
    if len(frame) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame exceeds maximum size of {MAX_FRAME_BYTES} bytes")
    if not frame:
        raise ProtocolError("frame must not be empty")
    return frame


def decode_json_object(frame: bytes) -> dict[str, Any]:
    try:
        text = frame.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("frame must be valid UTF-8") from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ProtocolError(f"invalid JSON: {error.msg}") from error
    if not isinstance(value, dict):
        raise ProtocolError("frame must be a JSON object")
    return value


def encode_frame(value: dict[str, Any]) -> bytes:
    frame = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    if len(frame) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame exceeds maximum size of {MAX_FRAME_BYTES} bytes")
    return frame + b"\n"


def _validate_protocol_header(
    protocol_version: object, message_type: object, expected_message_type: str
) -> None:
    _validate_integer(protocol_version, "protocol_version")
    if protocol_version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol_version {protocol_version}; expected {PROTOCOL_VERSION}"
        )
    if message_type != expected_message_type:
        raise ProtocolError(f"unsupported message_type {message_type}")


def _validate_versions(versions: object) -> None:
    if not isinstance(versions, tuple) or not versions:
        raise ProtocolError("supported_versions must be a non-empty array")
    for version in versions:
        _validate_positive_integer(version, "supported version")
    if len(set(versions)) != len(versions):
        raise ProtocolError("supported_versions must not contain duplicates")


def _validate_capabilities(capabilities: object) -> None:
    if not isinstance(capabilities, tuple):
        raise ProtocolError("capabilities must be an array")
    for capability in capabilities:
        _validate_non_empty_string(capability, "capability")
    if len(set(capabilities)) != len(capabilities):
        raise ProtocolError("capabilities must not contain duplicates")


def _required_list(value: dict[str, Any], field_name: str) -> list[Any]:
    field_value = value.get(field_name)
    if not isinstance(field_value, list):
        raise ProtocolError(f"{field_name} must be an array")
    return field_value


def _required_mapping(value: dict[str, Any], field_name: str) -> dict[str, Any]:
    field_value = value.get(field_name)
    if not isinstance(field_value, dict):
        raise ProtocolError(f"{field_name} must be an object")
    return field_value


def _required_string(value: dict[str, Any], field_name: str) -> str:
    field_value = value.get(field_name)
    _validate_non_empty_string(field_value, field_name)
    return field_value


def _required_integer(value: dict[str, Any], field_name: str) -> int:
    field_value = value.get(field_name)
    _validate_integer(field_value, field_name)
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
