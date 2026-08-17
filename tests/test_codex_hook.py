import unittest

from deskhelm_bridge.codex_hook import event_from_hook
from deskhelm_bridge.event import AgentState


class CodexHookTests(unittest.TestCase):
    def test_permission_request_maps_to_waiting_approval(self) -> None:
        event = event_from_hook(
            {"hook_event_name": "PermissionRequest", "session_id": "session-1"},
            slot=1,
            label="api",
        )

        self.assertEqual(event.agent_id, "codex:session-1")
        self.assertEqual(event.slot, 1)
        self.assertEqual(event.state, AgentState.WAITING_APPROVAL)
        self.assertEqual(event.label, "api")

    def test_unknown_event_defaults_to_thinking(self) -> None:
        event = event_from_hook({"event": "FutureEvent"}, slot=0, label="codex")

        self.assertEqual(event.state, AgentState.THINKING)


if __name__ == "__main__":
    unittest.main()
