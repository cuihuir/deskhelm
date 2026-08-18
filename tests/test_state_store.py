import unittest

from deskhelm_bridge.event import AgentEvent, AgentState
from deskhelm_bridge.state_store import StateStore


class StateStoreTests(unittest.TestCase):
    def test_starts_with_offline_state_for_each_slot(self) -> None:
        store = StateStore(slot_count=3)

        self.assertEqual([event.state for event in store.snapshot()], [AgentState.OFFLINE] * 3)

    def test_update_changes_snapshot_and_notifies_subscriber(self) -> None:
        store = StateStore(slot_count=2)
        updates: list[tuple[AgentEvent, tuple[AgentEvent, ...]]] = []
        store.subscribe(lambda event, snapshot: updates.append((event, snapshot)))
        event = AgentEvent(agent_id="codex:one", slot=1, state=AgentState.RUNNING_TOOL)

        store.update(event)

        self.assertEqual(store.snapshot()[1], event)
        self.assertEqual(updates, [(event, store.snapshot())])

    def test_unsubscribe_stops_notifications(self) -> None:
        store = StateStore(slot_count=1)
        updates: list[AgentEvent] = []
        unsubscribe = store.subscribe(lambda event, snapshot: updates.append(event))
        unsubscribe()

        store.update(AgentEvent(agent_id="codex:one", slot=0, state=AgentState.IDLE))

        self.assertEqual(updates, [])

    def test_subscribe_with_snapshot_registers_before_returning_state(self) -> None:
        store = StateStore(slot_count=1)
        updates: list[AgentEvent] = []

        snapshot, unsubscribe = store.subscribe_with_snapshot(
            lambda changed, current: updates.append(changed)
        )
        event = AgentEvent(agent_id="codex", slot=0, state=AgentState.THINKING)
        store.update(event)
        unsubscribe()

        self.assertEqual(snapshot[0].state, AgentState.OFFLINE)
        self.assertEqual(updates, [event])

    def test_rejects_slot_outside_configured_store(self) -> None:
        store = StateStore(slot_count=1)

        with self.assertRaisesRegex(ValueError, "outside configured range"):
            store.update(AgentEvent(agent_id="overflow", slot=1, state=AgentState.THINKING))


if __name__ == "__main__":
    unittest.main()
