import unittest

from agent_io_bridge.event import AgentEvent, AgentState
from agent_io_bridge.session_registry import SessionKey, SessionRegistry


class SessionRegistryTests(unittest.TestCase):
    def test_assigns_first_available_slot(self) -> None:
        registry = SessionRegistry(slot_count=2)
        first = SessionKey(agent_id="codex", session_id="one", project_id="agent-io")
        second = SessionKey(agent_id="codex", session_id="two", project_id="agent-io")

        self.assertEqual(registry.assign(first), 0)
        self.assertEqual(registry.assign(second), 1)
        self.assertEqual(registry.slot_for(first), 0)

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


if __name__ == "__main__":
    unittest.main()
