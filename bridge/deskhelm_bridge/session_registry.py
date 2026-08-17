from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from threading import RLock
import time

from .event import AgentEvent


@dataclass(frozen=True, slots=True)
class SessionKey:
    agent_id: str
    session_id: str = ""
    project_id: str = ""

    def __post_init__(self) -> None:
        if not self.agent_id.strip():
            raise ValueError("agent_id must not be empty")


class SessionLifecycleState(StrEnum):
    ACTIVE = "active"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session: SessionKey
    slot: int
    state: SessionLifecycleState
    registered_at: int
    updated_at: int
    disconnected_at: int | None = None


@dataclass(slots=True)
class SessionRegistry:
    slot_count: int
    _slots_by_session: dict[SessionKey, int] = field(default_factory=dict, init=False, repr=False)
    _sessions_by_slot: dict[int, SessionKey] = field(default_factory=dict, init=False, repr=False)
    _records: dict[SessionKey, SessionRecord] = field(default_factory=dict, init=False, repr=False)
    _focused_session: SessionKey | None = field(default=None, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.slot_count < 1:
            raise ValueError("slot_count must be at least 1")

    def register(
        self,
        session: SessionKey,
        preferred_slot: int | None = None,
        *,
        now_ms: int | None = None,
    ) -> int:
        timestamp = self._timestamp(now_ms)
        with self._lock:
            current_slot = self._slots_by_session.get(session)
            if preferred_slot is not None:
                slot = preferred_slot
            elif current_slot is not None:
                slot = current_slot
            else:
                slot = self._first_free_slot()
            self._validate_slot(slot)

            previous_session = self._sessions_by_slot.get(slot)
            if previous_session is not None and previous_session != session:
                self._remove(previous_session)

            if current_slot is not None and current_slot != slot:
                del self._sessions_by_slot[current_slot]

            self._slots_by_session[session] = slot
            self._sessions_by_slot[slot] = session
            previous_record = self._records.get(session)
            self._records[session] = SessionRecord(
                session=session,
                slot=slot,
                state=SessionLifecycleState.ACTIVE,
                registered_at=(
                    previous_record.registered_at if previous_record is not None else timestamp
                ),
                updated_at=timestamp,
            )
            return slot

    def assign(self, session: SessionKey, preferred_slot: int | None = None) -> int:
        return self.register(session, preferred_slot)

    def restore(
        self,
        session: SessionKey,
        preferred_slot: int | None = None,
        *,
        now_ms: int | None = None,
    ) -> int:
        return self.register(session, preferred_slot, now_ms=now_ms)

    def observe(self, event: AgentEvent) -> SessionKey:
        session = SessionKey(agent_id=event.agent_id)
        self.register(session, preferred_slot=event.slot, now_ms=event.updated_at)
        return session

    def focus(self, session: SessionKey) -> None:
        with self._lock:
            record = self._records.get(session)
            if record is None:
                raise ValueError("cannot focus an unregistered session")
            if record.state is not SessionLifecycleState.ACTIVE:
                raise ValueError("cannot focus a disconnected session")
            self._focused_session = session

    def clear_focus(self) -> None:
        with self._lock:
            self._focused_session = None

    def focused_session(self) -> SessionKey | None:
        with self._lock:
            return self._focused_session

    def disconnect(self, session: SessionKey, *, now_ms: int | None = None) -> bool:
        timestamp = self._timestamp(now_ms)
        with self._lock:
            record = self._records.get(session)
            if record is None:
                return False
            self._records[session] = replace(
                record,
                state=SessionLifecycleState.DISCONNECTED,
                updated_at=max(record.updated_at, timestamp),
                disconnected_at=(
                    record.disconnected_at
                    if record.disconnected_at is not None
                    else timestamp
                ),
            )
            if self._focused_session == session:
                self._focused_session = None
            return True

    def expire_disconnected(
        self, retention_ms: int, *, now_ms: int | None = None
    ) -> tuple[SessionKey, ...]:
        if retention_ms < 0:
            raise ValueError("retention_ms must not be negative")
        threshold = self._timestamp(now_ms) - retention_ms
        with self._lock:
            expired = tuple(
                record.session
                for record in self._records.values()
                if record.state is SessionLifecycleState.DISCONNECTED
                and record.disconnected_at is not None
                and record.disconnected_at <= threshold
            )
            for session in expired:
                self._remove(session)
            return expired

    def slot_for(self, session: SessionKey) -> int | None:
        with self._lock:
            return self._slots_by_session.get(session)

    def session_for(self, slot: int) -> SessionKey | None:
        self._validate_slot(slot)
        with self._lock:
            return self._sessions_by_slot.get(slot)

    def record_for(self, session: SessionKey) -> SessionRecord | None:
        with self._lock:
            return self._records.get(session)

    def snapshot(self) -> tuple[SessionRecord, ...]:
        with self._lock:
            return tuple(sorted(self._records.values(), key=lambda record: record.slot))

    def release(self, session: SessionKey) -> None:
        with self._lock:
            self._remove(session)

    def _first_free_slot(self) -> int:
        for slot in range(self.slot_count):
            if slot not in self._sessions_by_slot:
                return slot
        raise ValueError("no display slots are available")

    def _validate_slot(self, slot: int) -> None:
        if slot < 0 or slot >= self.slot_count:
            raise ValueError(f"slot {slot} is outside configured range 0-{self.slot_count - 1}")

    def _remove(self, session: SessionKey) -> None:
        slot = self._slots_by_session.pop(session, None)
        if slot is not None:
            self._sessions_by_slot.pop(slot, None)
        self._records.pop(session, None)
        if self._focused_session == session:
            self._focused_session = None

    @staticmethod
    def _timestamp(now_ms: int | None) -> int:
        return now_ms if now_ms is not None else int(time.time() * 1000)
