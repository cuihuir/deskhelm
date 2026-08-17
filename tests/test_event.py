import unittest

from deskhelm_bridge.event import AgentEvent, AgentState, ProtocolError


class AgentEventTests(unittest.TestCase):
    def test_round_trip_preserves_normalized_event(self) -> None:
        event = AgentEvent(
            agent_id="project-a:codex:1",
            slot=2,
            state=AgentState.RUNNING_TOOL,
            label="backend",
            progress=0.5,
            updated_at=1234,
        )

        self.assertEqual(AgentEvent.from_json(event.to_json()), event)

    def test_rejects_unknown_state(self) -> None:
        with self.assertRaises(ProtocolError):
            AgentEvent.from_dict({"agent_id": "a", "slot": 0, "state": "sleeping"})

    def test_rejects_wrong_protocol_version(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "unsupported protocol_version"):
            AgentEvent.from_dict(
                {
                    "agent_id": "a",
                    "slot": 0,
                    "state": "idle",
                    "protocol_version": 2,
                }
            )

    def test_rejects_invalid_progress(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "progress"):
            AgentEvent(agent_id="a", slot=0, state=AgentState.IDLE, progress=1.1)


if __name__ == "__main__":
    unittest.main()
