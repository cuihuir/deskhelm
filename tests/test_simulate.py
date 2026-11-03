from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from deskhelm_client import BridgeClient  # noqa: E402
from deskhelm_core.server import BridgeServer  # noqa: E402
from deskhelm_host import BridgeLink  # noqa: E402
from deskhelm_host.simulate import DemoSession  # noqa: E402
from deskhelm_protocol import SessionKey  # noqa: E402


KEY = SessionKey("simulate", "demo", "simulate")


def _wait(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met")
        time.sleep(0.02)


class DemoSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "bridge.sock"
        server = BridgeServer(path, tick_seconds=0.05)
        server.start()
        self.addCleanup(server.close)
        self.driver = DemoSession(step_seconds=0.01)
        link = BridgeLink(path, KEY, self.driver, client_id="host-demo", reconnect_initial=0.05, reconnect_max=0.1)
        link.start()
        self.addCleanup(link.close)
        self.assertTrue(link.opened.wait(3))
        self.driver.start(link.emit)
        self.addCleanup(self.driver.close)
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.client = BridgeClient(path, client_id="web", interaction=True, on_interaction=self._event)
        self.client.start()
        self.addCleanup(self.client.close)
        self.assertTrue(self.client.wait_ready(3))
        _wait(lambda: (self.client.console().session(KEY) or {}).get("activity") == "idle")

    def _event(self, event: dict, replay: bool) -> None:
        with self.lock:
            self.events.append(event)

    def _kinds(self) -> list[str]:
        with self.lock:
            return [event["kind"] + ("/" + event["payload"]["role"] if event["kind"] == "message" else "")
                    for event in self.events]

    def test_a_prompt_runs_a_scripted_turn(self) -> None:
        result = self.client.command("submit_prompt", KEY, {"text": "hello"})
        _wait(lambda: "task_completed" in self._kinds())

        self.assertEqual(result.code, "dispatched")
        self.assertEqual(self._kinds(), ["message/user", "tool", "tool", "tool", "message/assistant",
                                         "message/assistant", "task_completed"])
        with self.lock:
            self.assertIn("hello", self.events[-2]["payload"]["text"])

    def test_an_approval_prompt_waits_for_the_decision(self) -> None:
        self.client.command("submit_prompt", KEY, {"text": "please approve this"})
        _wait(lambda: bool(self.client.console().approvals(KEY)))
        ticket = self.client.console().approvals(KEY)[0]
        decided = self.client.command("reject", KEY, {"approval_id": ticket["approval_id"]})
        _wait(lambda: "task_completed" in self._kinds())

        self.assertEqual(decided.code, "dispatched")
        with self.lock:
            self.assertIn("被拒绝", self.events[-2]["payload"]["text"])

    def test_an_interrupt_ends_the_turn_and_cancels_its_approval(self) -> None:
        self.client.command("submit_prompt", KEY, {"text": "approve it"})
        _wait(lambda: bool(self.client.console().approvals(KEY)))
        stopped = self.client.command("interrupt", KEY)
        _wait(lambda: "task_failed" in self._kinds())
        _wait(lambda: not self.client.console().approvals(KEY))

        self.assertEqual(stopped.code, "dispatched")
        with self.lock:
            self.assertEqual(self.events[-1]["payload"]["error_code"], "interrupted")
        self.assertEqual(self.client.command("interrupt", KEY).code, "undelivered")


if __name__ == "__main__":
    unittest.main()
