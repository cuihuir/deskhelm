from io import StringIO
import unittest

from deskhelm_bridge.display import SlotPanel
from deskhelm_bridge.event import AgentEvent, AgentState
from deskhelm_bridge.state_store import StateStore


class SlotPanelTests(unittest.TestCase):
    def test_initial_render_contains_four_offline_slots(self) -> None:
        output = StringIO()
        panel = SlotPanel(stream=output, color=False, live=True)
        store = StateStore(slot_count=4)

        panel.render(store.snapshot())

        rendered = output.getvalue()
        self.assertEqual(rendered.count("offline"), 4)
        for slot in range(1, 5):
            self.assertIn(f"[{slot}]", rendered)

    def test_renders_state_store_updates(self) -> None:
        output = StringIO()
        panel = SlotPanel(stream=output, color=False, live=False)
        store = StateStore(slot_count=4)
        store.subscribe(panel.on_state_change)

        store.update(AgentEvent(agent_id="worker", slot=2, state=AgentState.THINKING))

        self.assertIn("slot=2", output.getvalue())
        self.assertIn("state=thinking", output.getvalue())


if __name__ == "__main__":
    unittest.main()
