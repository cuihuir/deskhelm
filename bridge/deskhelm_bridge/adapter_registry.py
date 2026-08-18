from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
import time

from .adapter import AdapterCapability, AdapterSessionAction, AdapterSessionEvent
from .event import AgentEvent
from .interaction import InteractionEvent, InteractionKind
from .session_registry import SessionKey, SessionLifecycleState, SessionRegistry


@dataclass(frozen=True, slots=True)
class AdapterSessionRecord:
    session: SessionKey
    owner_id: str
    adapter_id: str
    adapter_version: str
    runtime_name: str
    runtime_version: str
    capabilities: tuple[AdapterCapability, ...]
    slot: int


@dataclass(slots=True)
class AdapterRegistry:
    session_registry: SessionRegistry
    _records: dict[SessionKey, AdapterSessionRecord] = field(
        default_factory=dict, init=False, repr=False
    )
    _sessions_by_owner: dict[str, set[SessionKey]] = field(
        default_factory=dict, init=False, repr=False
    )
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def apply(self, owner_id: str, event: AdapterSessionEvent) -> int | None:
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        session = self._session_key(event)
        with self._lock:
            if event.action is AdapterSessionAction.REGISTER:
                return self._register(owner_id, session, event)
            record = self._owned_record(owner_id, session)
            if event.action is AdapterSessionAction.DISCONNECT:
                self.session_registry.disconnect(session, now_ms=event.occurred_at)
                return record.slot
            self.session_registry.release(session)
            self._remove_record(record)
            return None

    def disconnect_owner(
        self, owner_id: str, *, now_ms: int | None = None
    ) -> tuple[SessionKey, ...]:
        timestamp = self._timestamp(now_ms)
        with self._lock:
            sessions = tuple(self._sessions_by_owner.pop(owner_id, set()))
            disconnected: list[SessionKey] = []
            for session in sessions:
                record = self._records.get(session)
                if record is None or record.owner_id != owner_id:
                    continue
                if self.session_registry.disconnect(session, now_ms=timestamp):
                    disconnected.append(session)
            return tuple(disconnected)

    def validate_state_event(self, owner_id: str, event: AgentEvent) -> None:
        with self._lock:
            matches = [
                record
                for record in self._records.values()
                if record.owner_id == owner_id
                and record.session.agent_id == event.agent_id
                and record.slot == event.slot
                and self._is_active(record.session)
                and AdapterCapability.STATE_EVENTS in record.capabilities
            ]
        if not matches:
            raise ValueError("state event does not target an active owned session")

    def validate_interaction_event(
        self, owner_id: str, event: InteractionEvent
    ) -> None:
        session = SessionKey(
            agent_id=event.agent_id,
            session_id=event.session_id,
            project_id=event.project_id,
        )
        with self._lock:
            record = self._records.get(session)
            if (
                record is None
                or record.owner_id != owner_id
                or not self._is_active(session)
                or self._interaction_capability(event) not in record.capabilities
            ):
                raise ValueError(
                    "interaction event does not target an active owned session"
                )

    def record_for(self, session: SessionKey) -> AdapterSessionRecord | None:
        with self._lock:
            return self._records.get(session)

    def _register(
        self,
        owner_id: str,
        session: SessionKey,
        event: AdapterSessionEvent,
    ) -> int:
        previous_at_slot = (
            self.session_registry.session_for(event.preferred_slot)
            if event.preferred_slot is not None
            else None
        )
        slot = self.session_registry.register(
            session,
            preferred_slot=event.preferred_slot,
            now_ms=event.occurred_at,
        )
        if previous_at_slot is not None and previous_at_slot != session:
            previous_record = self._records.get(previous_at_slot)
            if previous_record is not None:
                self._remove_record(previous_record)

        previous_record = self._records.get(session)
        if previous_record is not None:
            owner_sessions = self._sessions_by_owner.get(previous_record.owner_id)
            if owner_sessions is not None:
                owner_sessions.discard(session)
                if not owner_sessions:
                    del self._sessions_by_owner[previous_record.owner_id]

        record = AdapterSessionRecord(
            session=session,
            owner_id=owner_id,
            adapter_id=event.adapter_id,
            adapter_version=event.adapter_version,
            runtime_name=event.runtime_name,
            runtime_version=event.runtime_version,
            capabilities=event.capabilities,
            slot=slot,
        )
        self._records[session] = record
        self._sessions_by_owner.setdefault(owner_id, set()).add(session)
        return slot

    def _owned_record(
        self, owner_id: str, session: SessionKey
    ) -> AdapterSessionRecord:
        record = self._records.get(session)
        if record is None:
            raise ValueError("adapter session is not registered")
        if record.owner_id != owner_id:
            raise ValueError("adapter session is owned by another connection")
        return record

    def _remove_record(self, record: AdapterSessionRecord) -> None:
        self._records.pop(record.session, None)
        owner_sessions = self._sessions_by_owner.get(record.owner_id)
        if owner_sessions is None:
            return
        owner_sessions.discard(record.session)
        if not owner_sessions:
            del self._sessions_by_owner[record.owner_id]

    def _is_active(self, session: SessionKey) -> bool:
        lifecycle = self.session_registry.record_for(session)
        return (
            lifecycle is not None
            and lifecycle.state is SessionLifecycleState.ACTIVE
        )

    @staticmethod
    def _interaction_capability(event: InteractionEvent) -> AdapterCapability:
        if event.kind is InteractionKind.TOOL:
            return AdapterCapability.TOOL_EVENTS
        if event.kind is InteractionKind.APPROVAL_REQUEST:
            return AdapterCapability.APPROVAL_REQUESTS
        return AdapterCapability.INTERACTION_EVENTS

    @staticmethod
    def _session_key(event: AdapterSessionEvent) -> SessionKey:
        return SessionKey(
            agent_id=event.agent_id,
            session_id=event.session_id,
            project_id=event.project_id,
        )

    @staticmethod
    def _timestamp(now_ms: int | None) -> int:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        if (
            not isinstance(timestamp, int)
            or isinstance(timestamp, bool)
            or timestamp <= 0
        ):
            raise ValueError("now_ms must be a positive integer")
        return timestamp
