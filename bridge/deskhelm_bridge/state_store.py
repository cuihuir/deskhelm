from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock

from .event import AgentEvent, AgentState


StateSubscriber = Callable[[AgentEvent, tuple[AgentEvent, ...]], None]


@dataclass(slots=True)
class StateStore:
    slot_count: int
    _events: list[AgentEvent] = field(init=False, repr=False)
    _subscribers: list[StateSubscriber] = field(default_factory=list, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.slot_count < 1:
            raise ValueError("slot_count must be at least 1")
        self._events = [
            AgentEvent(agent_id=f"slot-{slot + 1}", slot=slot, state=AgentState.OFFLINE)
            for slot in range(self.slot_count)
        ]

    def snapshot(self) -> tuple[AgentEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def update(self, event: AgentEvent) -> None:
        if event.slot >= self.slot_count:
            raise ValueError(
                f"slot {event.slot} is outside configured range 0-{self.slot_count - 1}"
            )

        with self._lock:
            self._events[event.slot] = event
            snapshot = tuple(self._events)
            subscribers = tuple(self._subscribers)

        for subscriber in subscribers:
            subscriber(event, snapshot)

    def subscribe(self, subscriber: StateSubscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        return self._unsubscribe_callback(subscriber)

    def subscribe_with_snapshot(
        self, subscriber: StateSubscriber
    ) -> tuple[tuple[AgentEvent, ...], Callable[[], None]]:
        with self._lock:
            self._subscribers.append(subscriber)
            snapshot = tuple(self._events)

        return snapshot, self._unsubscribe_callback(subscriber)

    def _unsubscribe_callback(
        self, subscriber: StateSubscriber
    ) -> Callable[[], None]:
        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe
