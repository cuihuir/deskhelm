from __future__ import annotations

from dataclasses import dataclass, field
import json
from queue import Empty, Full, Queue
from threading import Event, Lock
from typing import Any
import uuid

from .event import AgentEvent, PROTOCOL_VERSION, ProtocolError


STATE_SNAPSHOT_MESSAGE_TYPE = "state_snapshot"
STATE_UPDATE_MESSAGE_TYPE = "state_update"


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    stream_id: str
    subscription_id: str
    events: tuple[AgentEvent, ...]
    sequence: int = 0
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = STATE_SNAPSHOT_MESSAGE_TYPE

    def __post_init__(self) -> None:
        _validate_header(
            self.protocol_version, self.message_type, STATE_SNAPSHOT_MESSAGE_TYPE
        )
        _validate_non_empty_string(self.stream_id, "stream_id")
        _validate_non_empty_string(self.subscription_id, "subscription_id")
        _validate_integer(self.sequence, "sequence")
        if self.sequence != 0:
            raise ProtocolError("snapshot sequence must be zero")
        if not isinstance(self.events, tuple):
            raise ProtocolError("snapshot events must be an array")
        if not all(isinstance(event, AgentEvent) for event in self.events):
            raise ProtocolError("snapshot events must contain AgentEvent v1 objects")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "stream_id": self.stream_id,
            "subscription_id": self.subscription_id,
            "sequence": self.sequence,
            "events": [event.to_dict() for event in self.events],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StateSnapshot:
        events_value = _required_list(value, "events")
        return cls(
            protocol_version=_required_integer(value, "protocol_version"),
            message_type=_required_string(value, "message_type"),
            stream_id=_required_string(value, "stream_id"),
            subscription_id=_required_string(value, "subscription_id"),
            sequence=_required_integer(value, "sequence"),
            events=tuple(AgentEvent.from_dict(event) for event in events_value),
        )

    @classmethod
    def from_json(cls, line: str) -> StateSnapshot:
        return cls.from_dict(_decode_object(line, "state snapshot"))


@dataclass(frozen=True, slots=True)
class StateUpdate:
    stream_id: str
    subscription_id: str
    sequence: int
    event: AgentEvent
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = STATE_UPDATE_MESSAGE_TYPE

    def __post_init__(self) -> None:
        _validate_header(
            self.protocol_version, self.message_type, STATE_UPDATE_MESSAGE_TYPE
        )
        _validate_non_empty_string(self.stream_id, "stream_id")
        _validate_non_empty_string(self.subscription_id, "subscription_id")
        _validate_positive_integer(self.sequence, "sequence")
        if not isinstance(self.event, AgentEvent):
            raise ProtocolError("state update event must be an AgentEvent v1 object")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "stream_id": self.stream_id,
            "subscription_id": self.subscription_id,
            "sequence": self.sequence,
            "event": self.event.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StateUpdate:
        return cls(
            protocol_version=_required_integer(value, "protocol_version"),
            message_type=_required_string(value, "message_type"),
            stream_id=_required_string(value, "stream_id"),
            subscription_id=_required_string(value, "subscription_id"),
            sequence=_required_positive_integer(value, "sequence"),
            event=AgentEvent.from_dict(_required_mapping(value, "event")),
        )

    @classmethod
    def from_json(cls, line: str) -> StateUpdate:
        return cls.from_dict(_decode_object(line, "state update"))


@dataclass(slots=True)
class StateSubscriberQueue:
    stream_id: str
    max_queue_frames: int
    subscription_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _updates: Queue[StateUpdate] = field(init=False, repr=False)
    _overflowed: Event = field(default_factory=Event, init=False, repr=False)
    _sequence: int = field(default=0, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.stream_id, "stream_id")
        _validate_non_empty_string(self.subscription_id, "subscription_id")
        _validate_positive_integer(self.max_queue_frames, "max_queue_frames")
        self._updates = Queue(maxsize=self.max_queue_frames)

    def snapshot(self, events: tuple[AgentEvent, ...]) -> StateSnapshot:
        return StateSnapshot(
            stream_id=self.stream_id,
            subscription_id=self.subscription_id,
            events=events,
        )

    def enqueue(
        self, changed: AgentEvent, snapshot: tuple[AgentEvent, ...]
    ) -> None:
        del snapshot
        if self._overflowed.is_set():
            return
        with self._lock:
            self._sequence += 1
            update = StateUpdate(
                stream_id=self.stream_id,
                subscription_id=self.subscription_id,
                sequence=self._sequence,
                event=changed,
            )
            try:
                self._updates.put_nowait(update)
            except Full:
                self._overflowed.set()

    def next_update(self, timeout: float) -> StateUpdate | None:
        try:
            return self._updates.get(timeout=timeout)
        except Empty:
            return None

    def overflowed(self) -> bool:
        return self._overflowed.is_set()


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


def _required_mapping(value: dict[str, Any], field_name: str) -> dict[str, Any]:
    field_value = value.get(field_name)
    if not isinstance(field_value, dict):
        raise ProtocolError(f"{field_name} must be an object")
    return field_value


def _required_list(value: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    field_value = value.get(field_name)
    if not isinstance(field_value, list) or not all(
        isinstance(item, dict) for item in field_value
    ):
        raise ProtocolError(f"{field_name} must be an array of objects")
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
