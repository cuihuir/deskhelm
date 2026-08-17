import unittest

from deskhelm_bridge.event import AgentEvent, AgentState
from deskhelm_bridge.session_registry import (
    SessionKey,
    SessionLifecycleState,
    SessionRegistry,
)


class SessionRegistryTests(unittest.TestCase):
    def test_assigns_first_available_slot(self) -> None:
        registry = SessionRegistry(slot_count=2)
        first = SessionKey(agent_id="codex", session_id="one", project_id="deskhelm")
        second = SessionKey(agent_id="codex", session_id="two", project_id="deskhelm")

        self.assertEqual(registry.assign(first), 0)
        self.assertEqual(registry.assign(second), 1)
        self.assertEqual(registry.slot_for(first), 0)
        self.assertEqual(registry.record_for(first).state, SessionLifecycleState.ACTIVE)

    def test_preferred_slot_replaces_previous_projection(self) -> None:
        registry = SessionRegistry(slot_count=2)
        old = SessionKey(agent_id="codex:old")
        new = SessionKey(agent_id="codex:new")
        registry.assign(old, preferred_slot=1)

        registry.assign(new, preferred_slot=1)

        self.assertIsNone(registry.slot_for(old))
        self.assertEqual(registry.session_for(1), new)

    def test_observes_legacy_event_without_changing_protocol(self) -> None:
        registry = SessionRegistry(slot_count=2)
        event = AgentEvent(agent_id="codex:legacy", slot=1, state=AgentState.THINKING)

        session = registry.observe(event)

        self.assertEqual(session, SessionKey(agent_id="codex:legacy"))
        self.assertEqual(registry.slot_for(session), 1)

    def test_rejects_dynamic_assignment_when_all_slots_are_used(self) -> None:
        registry = SessionRegistry(slot_count=1)
        registry.assign(SessionKey(agent_id="codex:one"))

        with self.assertRaisesRegex(ValueError, "no display slots"):
            registry.assign(SessionKey(agent_id="codex:two"))

    def test_disconnect_clears_focus_but_retains_slot(self) -> None:
        registry = SessionRegistry(slot_count=1)
        session = SessionKey(agent_id="codex", session_id="one")
        registry.register(session, now_ms=100)
        registry.focus(session)

        self.assertTrue(registry.disconnect(session, now_ms=200))

        record = registry.record_for(session)
        self.assertEqual(record.state, SessionLifecycleState.DISCONNECTED)
        self.assertEqual(record.disconnected_at, 200)
        self.assertEqual(registry.slot_for(session), 0)
        self.assertIsNone(registry.focused_session())

    def test_repeated_disconnect_does_not_extend_expiration(self) -> None:
        registry = SessionRegistry(slot_count=1)
        session = SessionKey(agent_id="codex", session_id="one")
        registry.register(session, now_ms=100)

        registry.disconnect(session, now_ms=200)
        registry.disconnect(session, now_ms=400)

        record = registry.record_for(session)
        self.assertEqual(record.disconnected_at, 200)
        self.assertEqual(record.updated_at, 400)

    def test_rejects_focusing_missing_or_disconnected_session(self) -> None:
        registry = SessionRegistry(slot_count=1)
        session = SessionKey(agent_id="codex", session_id="one")

        with self.assertRaisesRegex(ValueError, "unregistered"):
            registry.focus(session)

        registry.register(session, now_ms=100)
        registry.disconnect(session, now_ms=200)

        with self.assertRaisesRegex(ValueError, "disconnected"):
            registry.focus(session)

    def test_restore_reuses_slot_without_restoring_focus(self) -> None:
        registry = SessionRegistry(slot_count=2)
        session = SessionKey(agent_id="codex", session_id="one")
        registry.register(session, preferred_slot=1, now_ms=100)
        registry.focus(session)
        registry.disconnect(session, now_ms=200)

        slot = registry.restore(session, now_ms=300)

        record = registry.record_for(session)
        self.assertEqual(slot, 1)
        self.assertEqual(record.state, SessionLifecycleState.ACTIVE)
        self.assertEqual(record.registered_at, 100)
        self.assertEqual(record.updated_at, 300)
        self.assertIsNone(record.disconnected_at)
        self.assertIsNone(registry.focused_session())

    def test_expire_releases_only_old_disconnected_sessions(self) -> None:
        registry = SessionRegistry(slot_count=2)
        old = SessionKey(agent_id="codex", session_id="old")
        recent = SessionKey(agent_id="codex", session_id="recent")
        registry.register(old, preferred_slot=0, now_ms=100)
        registry.register(recent, preferred_slot=1, now_ms=100)
        registry.disconnect(old, now_ms=200)
        registry.disconnect(recent, now_ms=900)

        expired = registry.expire_disconnected(retention_ms=500, now_ms=1000)

        self.assertEqual(expired, (old,))
        self.assertIsNone(registry.record_for(old))
        self.assertEqual(registry.slot_for(recent), 1)

    def test_legacy_event_timestamp_updates_session_record(self) -> None:
        registry = SessionRegistry(slot_count=1)
        event = AgentEvent(
            agent_id="codex:legacy",
            slot=0,
            state=AgentState.THINKING,
            updated_at=1234,
        )

        session = registry.observe(event)

        self.assertEqual(registry.record_for(session).updated_at, 1234)

    def test_slot_replacement_clears_previous_focus(self) -> None:
        registry = SessionRegistry(slot_count=1)
        old = SessionKey(agent_id="codex:old")
        new = SessionKey(agent_id="codex:new")
        registry.register(old, preferred_slot=0, now_ms=100)
        registry.focus(old)

        registry.register(new, preferred_slot=0, now_ms=200)

        self.assertIsNone(registry.record_for(old))
        self.assertIsNone(registry.focused_session())

    def test_rejects_negative_expiration_retention(self) -> None:
        registry = SessionRegistry(slot_count=1)

        with self.assertRaisesRegex(ValueError, "retention_ms"):
            registry.expire_disconnected(-1)


if __name__ == "__main__":
    unittest.main()
