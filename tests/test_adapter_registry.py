import json
from pathlib import Path
import unittest

from deskhelm_bridge.adapter import AdapterSessionEvent
from deskhelm_bridge.adapter_registry import AdapterRegistry
from deskhelm_bridge.event import AgentEvent, AgentState
from deskhelm_bridge.interaction import InteractionEvent
from deskhelm_bridge.session_registry import (
    SessionKey,
    SessionLifecycleState,
    SessionRegistry,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "protocol"


class AdapterRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = SessionRegistry(slot_count=3)
        self.adapters = AdapterRegistry(self.sessions)
        self.session = SessionKey("codex", "session-42", "deskhelm")

    def test_register_disconnect_restore_and_release(self) -> None:
        register = self._adapter_event("register.json")
        disconnect = self._adapter_event("disconnect.json")
        release = self._adapter_event("release.json")

        slot = self.adapters.apply("owner-1", register)
        self.assertEqual(slot, 1)
        self.assertEqual(
            self.sessions.record_for(self.session).state,
            SessionLifecycleState.ACTIVE,
        )

        retained_slot = self.adapters.apply("owner-1", disconnect)
        self.assertEqual(retained_slot, 1)
        self.assertEqual(
            self.sessions.record_for(self.session).state,
            SessionLifecycleState.DISCONNECTED,
        )

        self.adapters.apply("owner-1", register)
        self.assertEqual(
            self.sessions.record_for(self.session).state,
            SessionLifecycleState.ACTIVE,
        )

        self.assertIsNone(self.adapters.apply("owner-1", release))
        self.assertIsNone(self.sessions.record_for(self.session))
        self.assertIsNone(self.adapters.record_for(self.session))

    def test_old_owner_disconnect_does_not_affect_replacement_owner(self) -> None:
        register = self._adapter_event("register.json")
        self.adapters.apply("owner-old", register)
        self.adapters.apply("owner-new", register)

        disconnected = self.adapters.disconnect_owner("owner-old", now_ms=1787035001000)

        self.assertEqual(disconnected, ())
        self.assertEqual(
            self.sessions.record_for(self.session).state,
            SessionLifecycleState.ACTIVE,
        )
        self.assertEqual(self.adapters.record_for(self.session).owner_id, "owner-new")

    def test_connection_close_disconnects_only_owned_sessions(self) -> None:
        self.adapters.apply("owner-1", self._adapter_event("register.json"))

        disconnected = self.adapters.disconnect_owner(
            "owner-1", now_ms=1787035002000
        )

        self.assertEqual(disconnected, (self.session,))
        self.assertEqual(
            self.sessions.record_for(self.session).state,
            SessionLifecycleState.DISCONNECTED,
        )

    def test_validates_state_and_interaction_ownership(self) -> None:
        self.adapters.apply("owner-1", self._adapter_event("register.json"))
        state = AgentEvent(
            agent_id="codex", slot=1, state=AgentState.THINKING
        )
        interaction = self._interaction_event("message-delta.json")

        self.adapters.validate_state_event("owner-1", state)
        self.adapters.validate_interaction_event("owner-1", interaction)

        with self.assertRaisesRegex(ValueError, "active owned"):
            self.adapters.validate_state_event("owner-other", state)
        with self.assertRaisesRegex(ValueError, "active owned"):
            self.adapters.validate_interaction_event("owner-other", interaction)

    def test_disconnect_and_release_require_connection_ownership(self) -> None:
        self.adapters.apply("owner-1", self._adapter_event("register.json"))

        with self.assertRaisesRegex(ValueError, "another connection"):
            self.adapters.apply("owner-2", self._adapter_event("disconnect.json"))

    @staticmethod
    def _adapter_event(name: str) -> AdapterSessionEvent:
        value = json.loads(
            (FIXTURE_DIR / "adapter-session-v1" / name).read_text(encoding="utf-8")
        )
        return AdapterSessionEvent.from_dict(value)

    @staticmethod
    def _interaction_event(name: str) -> InteractionEvent:
        value = json.loads(
            (FIXTURE_DIR / "interaction-v1" / name).read_text(encoding="utf-8")
        )
        return InteractionEvent.from_dict(value)


if __name__ == "__main__":
    unittest.main()
