import json
from pathlib import Path
import unittest

from deskhelm_bridge.event import AgentEvent, AgentState, ProtocolError
from deskhelm_bridge.subscription import (
    StateSnapshot,
    StateSubscriberQueue,
    StateUpdate,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "protocol" / "subscription-v1"


class StateSubscriptionTests(unittest.TestCase):
    def test_protocol_fixtures_round_trip(self) -> None:
        snapshot_value = self._fixture("state-snapshot.json")
        snapshot = StateSnapshot.from_dict(snapshot_value)
        self.assertEqual(snapshot.to_dict(), snapshot_value)
        self.assertEqual(StateSnapshot.from_json(snapshot.to_json()), snapshot)

        update_value = self._fixture("state-update.json")
        update = StateUpdate.from_dict(update_value)
        self.assertEqual(update.to_dict(), update_value)
        self.assertEqual(StateUpdate.from_json(update.to_json()), update)

    def test_snapshot_sequence_is_zero(self) -> None:
        value = self._fixture("state-snapshot.json")
        value["sequence"] = 1

        with self.assertRaisesRegex(ProtocolError, "snapshot sequence"):
            StateSnapshot.from_dict(value)

    def test_update_sequence_is_positive(self) -> None:
        value = self._fixture("state-update.json")
        value["sequence"] = 0

        with self.assertRaisesRegex(ProtocolError, "sequence"):
            StateUpdate.from_dict(value)

    def test_queue_assigns_ordered_sequences(self) -> None:
        queue = StateSubscriberQueue(stream_id="stream-1", max_queue_frames=2)
        first = AgentEvent(agent_id="one", slot=0, state=AgentState.THINKING)
        second = AgentEvent(agent_id="two", slot=1, state=AgentState.COMPLETED)

        queue.enqueue(first, (first, second))
        queue.enqueue(second, (first, second))

        self.assertEqual(queue.next_update(0).sequence, 1)
        self.assertEqual(queue.next_update(0).sequence, 2)
        self.assertFalse(queue.overflowed())

    def test_queue_overflow_is_non_blocking_and_terminal(self) -> None:
        queue = StateSubscriberQueue(stream_id="stream-1", max_queue_frames=1)
        event = AgentEvent(agent_id="one", slot=0, state=AgentState.THINKING)

        queue.enqueue(event, (event,))
        queue.enqueue(event, (event,))

        self.assertTrue(queue.overflowed())
        self.assertEqual(queue.next_update(0).sequence, 1)

    @staticmethod
    def _fixture(name: str) -> dict[str, object]:
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
