from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from queue import Empty, Full, Queue
from threading import Event, Lock, RLock
from typing import Any
import uuid

from .event import PROTOCOL_VERSION, ProtocolError
from .interaction import InteractionEvent


INTERACTION_SUBSCRIPTION_STARTED_MESSAGE_TYPE = "interaction_subscription_started"
INTERACTION_UPDATE_MESSAGE_TYPE = "interaction_update"
InteractionSubscriber = Callable[[InteractionEvent], None]


@dataclass(frozen=True, slots=True)
class InteractionSubscriptionStarted:
    stream_id: str
    subscription_id: str
    sequence: int = 0
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = INTERACTION_SUBSCRIPTION_STARTED_MESSAGE_TYPE

    def __post_init__(self) -> None:
        _validate_header(
            self.protocol_version,
            self.message_type,
            INTERACTION_SUBSCRIPTION_STARTED_MESSAGE_TYPE,
        )
        _validate_non_empty_string(self.stream_id, "stream_id")
        _validate_non_empty_string(self.subscription_id, "subscription_id")
        _validate_integer(self.sequence, "sequence")
        if self.sequence != 0:
            raise ProtocolError("interaction subscription start sequence must be zero")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "stream_id": self.stream_id,
            "subscription_id": self.subscription_id,
            "sequence": self.sequence,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> InteractionSubscriptionStarted:
        return cls(
            protocol_version=_required_integer(value, "protocol_version"),
            message_type=_required_string(value, "message_type"),
            stream_id=_required_string(value, "stream_id"),
            subscription_id=_required_string(value, "subscription_id"),
            sequence=_required_integer(value, "sequence"),
        )

    @classmethod
    def from_json(cls, line: str) -> InteractionSubscriptionStarted:
        return cls.from_dict(_decode_object(line, "interaction subscription start"))


@dataclass(frozen=True, slots=True)
class InteractionUpdate:
    stream_id: str
    subscription_id: str
    sequence: int
    event: InteractionEvent
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = INTERACTION_UPDATE_MESSAGE_TYPE

    def __post_init__(self) -> None:
        _validate_header(
            self.protocol_version, self.message_type, INTERACTION_UPDATE_MESSAGE_TYPE
        )
        _validate_non_empty_string(self.stream_id, "stream_id")
        _validate_non_empty_string(self.subscription_id, "subscription_id")
        _validate_positive_integer(self.sequence, "sequence")
        if not isinstance(self.event, InteractionEvent):
            raise ProtocolError(
                "interaction update event must be an InteractionEvent v1 object"
            )

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
    def from_dict(cls, value: dict[str, Any]) -> InteractionUpdate:
        return cls(
            protocol_version=_required_integer(value, "protocol_version"),
            message_type=_required_string(value, "message_type"),
            stream_id=_required_string(value, "stream_id"),
            subscription_id=_required_string(value, "subscription_id"),
            sequence=_required_positive_integer(value, "sequence"),
            event=InteractionEvent.from_dict(_required_mapping(value, "event")),
        )

    @classmethod
    def from_json(cls, line: str) -> InteractionUpdate:
        return cls.from_dict(_decode_object(line, "interaction update"))


@dataclass(slots=True)
class InteractionSubscriberQueue:
    stream_id: str
    max_queue_frames: int
    subscription_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _updates: Queue[InteractionUpdate] = field(init=False, repr=False)
    _overflowed: Event = field(default_factory=Event, init=False, repr=False)
    _sequence: int = field(default=0, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.stream_id, "stream_id")
        _validate_non_empty_string(self.subscription_id, "subscription_id")
        _validate_positive_integer(self.max_queue_frames, "max_queue_frames")
        self._updates = Queue(maxsize=self.max_queue_frames)

    def started(self) -> InteractionSubscriptionStarted:
        return InteractionSubscriptionStarted(
            stream_id=self.stream_id,
            subscription_id=self.subscription_id,
        )

    def enqueue(self, event: InteractionEvent) -> None:
        if self._overflowed.is_set():
            return
        with self._lock:
            self._sequence += 1
            update = InteractionUpdate(
                stream_id=self.stream_id,
                subscription_id=self.subscription_id,
                sequence=self._sequence,
                event=event,
            )
            try:
                self._updates.put_nowait(update)
            except Full:
                self._overflowed.set()

    def next_update(self, timeout: float) -> InteractionUpdate | None:
        try:
            return self._updates.get(timeout=timeout)
        except Empty:
            return None

    def overflowed(self) -> bool:
        return self._overflowed.is_set()


@dataclass(slots=True)
class InteractionHub:
    _subscribers: list[InteractionSubscriber] = field(
        default_factory=list, init=False, repr=False
    )
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def subscribe(self, subscriber: InteractionSubscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def publish(self, event: InteractionEvent) -> None:
        if not isinstance(event, InteractionEvent):
            raise TypeError("event must be an InteractionEvent")
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            subscriber(event)


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
