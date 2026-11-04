from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from deskhelm_core import CoreLimits  # noqa: E402
from deskhelm_core.server import BridgeServer  # noqa: E402
from deskhelm_host import BridgeLink  # noqa: E402
from deskhelm_host.bridge_process import bridge_running, ensure_bridge  # noqa: E402
from deskhelm_protocol import (  # noqa: E402
    EffectKind,
    EffectOutcome,
    OpenRequest,
    RequestKind,
    SessionKey,
)
from helpers.v2 import FakeDriver, command, operator, session_view  # noqa: E402


KEY = SessionKey("fake", "session-1", "demo")


class BridgeLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "bridge.sock"
        self.server = self._server()
        self.driver = FakeDriver()
        self.link = BridgeLink(
            self.path, KEY, self.driver, client_id="host-test", reconnect_initial=0.05, reconnect_max=0.1
        )
        self.link.start()
        self.addCleanup(self.link.close)
        self.assertTrue(self.link.opened.wait(3))

    def _server(self, **limits) -> BridgeServer:
        server = BridgeServer(self.path, limits=CoreLimits(**limits) if limits else None, tick_seconds=0.05)
        server.start()
        self.addCleanup(server.close)
        return server

    def test_reopened_session_reports_the_driver_state(self) -> None:
        client = operator(self.path)
        self.addCleanup(client.close)

        view = session_view(client.next("snapshot"), KEY)
        self.assertEqual((view["lifecycle"], view["activity"]), ("active", "idle"))
        self.assertEqual(view["runtime"]["adapter_id"], "deskhelm-fake")

    def test_effects_reach_the_driver_and_answer_commands(self) -> None:
        client = operator(self.path)
        self.addCleanup(client.close)
        accepted = command(client, "submit_prompt", KEY, {"text": "hello"}, command_id="first")
        kind, payload = self.driver.performed.get(timeout=2)
        self.driver.outcome = EffectOutcome.UNDELIVERED
        undelivered = command(client, "interrupt", KEY, command_id="second")

        self.assertEqual((kind, payload["text"], payload["command_id"]), (EffectKind.SUBMIT, "hello", "first"))
        self.assertEqual(accepted["code"], "dispatched")
        self.assertEqual(undelivered["code"], "undelivered")

    def test_session_survives_a_bridge_restart(self) -> None:
        self.driver.turn_ref = "turn-7"
        self.driver.open_requests = (
            OpenRequest("rpc-1", RequestKind.APPROVAL, "请求使用工具 execute", int(time.time() * 1000) + 60_000, True,
                        "make deploy"),
        )
        self.server.close()
        self.link.opened.clear()
        self.server = self._server()
        self.assertTrue(self.link.opened.wait(3))
        client = operator(self.path)
        self.addCleanup(client.close)

        view = session_view(client.next("snapshot"), KEY)
        if view["activity"] != "waiting_approval":
            view = session_view(client.next("delta", where=lambda frame: session_view(frame, KEY)), KEY)
        self.assertEqual(view["activity"], "waiting_approval")
        self.assertEqual(view["approvals"][0]["summary"], "请求使用工具 execute")
        # The rebuilt approval reaches the operator again with its detail.
        client.send({"type": "subscribe", "stream": "interaction"})
        announced = client.next("interaction")["event"]
        self.assertEqual((announced["kind"], announced["payload"]["detail"]), ("approval_request", "make deploy"))

    def test_unknown_driver_outcome_is_left_to_the_bridge_deadline(self) -> None:
        self.server.close()
        self.link.opened.clear()
        self.server = self._server(effect_deadline_ms=200)
        self.assertTrue(self.link.opened.wait(3))
        self.driver.fail = True
        client = operator(self.path)
        self.addCleanup(client.close)

        result = command(client, "submit_prompt", KEY, {"text": "hello"})
        self.assertEqual(result["code"], "dispatch_failed")


class AutostartTests(unittest.TestCase):
    def test_bridge_is_started_only_before_the_first_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.sock"
            server = BridgeServer(path, tick_seconds=0.05)
            server.start()
            calls: list[Path] = []
            link = BridgeLink(
                path, KEY, FakeDriver(), client_id="host-test", ensure_bridge=calls.append,
                reconnect_initial=0.05, reconnect_max=0.1,
            )
            link.start()
            try:
                self.assertTrue(link.opened.wait(3))
                server.close()
                deadline = time.monotonic() + 3
                while link.opened.is_set() and time.monotonic() < deadline:
                    time.sleep(0.02)
                time.sleep(0.4)
            finally:
                link.close()

        self.assertEqual(calls, [path])


class EnsureBridgeTests(unittest.TestCase):
    def test_a_missing_bridge_is_started_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.sock"
            pid = ensure_bridge(path)
            try:
                again = ensure_bridge(path)
                running = bridge_running(path)
            finally:
                if pid is not None:
                    os.killpg(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 5
                    while path.exists() and time.monotonic() < deadline:
                        time.sleep(0.05)

        self.assertIsNotNone(pid)
        self.assertIsNone(again)
        self.assertTrue(running)


if __name__ == "__main__":
    unittest.main()
