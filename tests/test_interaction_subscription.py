import json
from pathlib import Path
import unittest

from deskhelm_bridge.event import ProtocolError
from deskhelm_bridge.interaction import InteractionEvent
from deskhelm_bridge.interaction_subscription import (
    InteractionHub,
    InteractionSubscriberQueue,
    InteractionSubscriptionStarted,
    InteractionUpdate,
)


FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "protocol"
    / "interaction-subscription-v1"
)
INTERACTION_FIXTURE_DIR = (
    Path(__file__).parent / "fixtures" / "protocol" / "interaction-v1"
)


class InteractionSubscriptionTests(unittest.TestCase):
    def test_protocol_fixtures_round_trip(self) -> None:
        started_value = self._fixture("started.json")
        started = InteractionSubscriptionStarted.from_dict(started_value)
        self.assertEqual(started.to_dict(), started_value)
        self.assertEqual(
            InteractionSubscriptionStarted.from_json(started.to_json()), started
        )

        update_value = self._fixture("update.json")
        update = InteractionUpdate.from_dict(update_value)
        self.assertEqual(update.to_dict(), update_value)
        self.assertEqual(InteractionUpdate.from_json(update.to_json()), update)

    def test_started_sequence_is_zero(self) -> None:
        value = self._fixture("started.json")
        value["sequence"] = 1

        with self.assertRaisesRegex(ProtocolError, "sequence must be zero"):
            InteractionSubscriptionStarted.from_dict(value)

    def test_update_sequence_is_positive(self) -> None:
        value = self._fixture("update.json")
        value["sequence"] = 0

        with self.assertRaisesRegex(ProtocolError, "sequence"):
            InteractionUpdate.from_dict(value)

    def test_hub_subscribes_and_unsubscribes_without_history(self) -> None:
        hub = InteractionHub()
        received: list[InteractionEvent] = []
        event = self._interaction_event()

        hub.publish(event)
        unsubscribe = hub.subscribe(received.append)
        hub.publish(event)
        unsubscribe()
        hub.publish(event)

        self.assertEqual(received, [event])

    def test_queue_assigns_ordered_sequences(self) -> None:
        queue = InteractionSubscriberQueue(
            stream_id="stream-1", max_queue_frames=2
        )
        event = self._interaction_event()

        queue.enqueue(event)
        queue.enqueue(event)

        self.assertEqual(queue.next_update(0).sequence, 1)
        self.assertEqual(queue.next_update(0).sequence, 2)
        self.assertFalse(queue.overflowed())

    def test_queue_overflow_is_non_blocking_and_terminal(self) -> None:
        queue = InteractionSubscriberQueue(
            stream_id="stream-1", max_queue_frames=1
        )
        event = self._interaction_event()

        queue.enqueue(event)
        queue.enqueue(event)
        queue.enqueue(event)

        self.assertTrue(queue.overflowed())
        self.assertEqual(queue.next_update(0).sequence, 1)
        self.assertIsNone(queue.next_update(0))

    @staticmethod
    def _fixture(name: str) -> dict[str, object]:
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))

    @staticmethod
    def _interaction_event() -> InteractionEvent:
        value = json.loads(
            (INTERACTION_FIXTURE_DIR / "message-delta.json").read_text(
                encoding="utf-8"
            )
        )
        return InteractionEvent.from_dict(value)


if __name__ == "__main__":
    unittest.main()
