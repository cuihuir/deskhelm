from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from .event import AgentEvent


@dataclass(frozen=True, slots=True)
class SessionKey:
    agent_id: str
    session_id: str = ""
    project_id: str = ""

    def __post_init__(self) -> None:
        if not self.agent_id.strip():
            raise ValueError("agent_id must not be empty")


@dataclass(slots=True)
class SessionRegistry:
    slot_count: int
    _slots_by_session: dict[SessionKey, int] = field(default_factory=dict, init=False, repr=False)
    _sessions_by_slot: dict[int, SessionKey] = field(default_factory=dict, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.slot_count < 1:
            raise ValueError("slot_count must be at least 1")

    def assign(self, session: SessionKey, preferred_slot: int | None = None) -> int:
        with self._lock:
            current_slot = self._slots_by_session.get(session)
            if preferred_slot is None and current_slot is not None:
                return current_slot

            slot = preferred_slot if preferred_slot is not None else self._first_free_slot()
            self._validate_slot(slot)

            previous_session = self._sessions_by_slot.get(slot)
            if previous_session is not None and previous_session != session:
                del self._slots_by_session[previous_session]

            if current_slot is not None and current_slot != slot:
                del self._sessions_by_slot[current_slot]

            self._slots_by_session[session] = slot
            self._sessions_by_slot[slot] = session
            return slot

    def observe(self, event: AgentEvent) -> SessionKey:
        session = SessionKey(agent_id=event.agent_id)
        self.assign(session, preferred_slot=event.slot)
        return session

    def slot_for(self, session: SessionKey) -> int | None:
        with self._lock:
            return self._slots_by_session.get(session)

    def session_for(self, slot: int) -> SessionKey | None:
        self._validate_slot(slot)
        with self._lock:
            return self._sessions_by_slot.get(slot)

    def release(self, session: SessionKey) -> None:
        with self._lock:
            slot = self._slots_by_session.pop(session, None)
            if slot is not None:
                self._sessions_by_slot.pop(slot, None)

    def _first_free_slot(self) -> int:
        for slot in range(self.slot_count):
            if slot not in self._sessions_by_slot:
                return slot
        raise ValueError("no display slots are available")

    def _validate_slot(self, slot: int) -> None:
        if slot < 0 or slot >= self.slot_count:
            raise ValueError(f"slot {slot} is outside configured range 0-{self.slot_count - 1}")
