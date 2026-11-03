from __future__ import annotations

from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from deskhelm_client import BridgeClient, ConsoleChange, activity_text, outcome_text, result_text  # noqa: E402
from deskhelm_client.__main__ import main as client_main  # noqa: E402
from deskhelm_core.server import BridgeServer  # noqa: E402
from deskhelm_host import BridgeLink  # noqa: E402
from deskhelm_protocol import EffectKind, FactKind, SessionKey, Tier  # noqa: E402
from helpers.v2 import FakeDriver  # noqa: E402


KEY = SessionKey("fake", "session-1", "demo")


def _wait(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met")
        time.sleep(0.02)


class _Recorder:
    def __init__(self) -> None:
        self.changes: list[ConsoleChange] = []
        self.events: list[tuple[dict, bool]] = []
        self.connections: list[bool] = []
        self.lock = threading.Lock()

    def console(self, change: ConsoleChange) -> None:
        with self.lock:
            self.changes.append(change)

    def interaction(self, event: dict, replay: bool) -> None:
        with self.lock:
            self.events.append((event, replay))

    def connection(self, connected: bool) -> None:
        with self.lock:
            self.connections.append(connected)


class BridgeClientTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "bridge.sock"
        self.server = self._server()
        self.driver = FakeDriver()
        self.link = BridgeLink(self.path, KEY, self.driver, client_id="host-test", reconnect_initial=0.05, reconnect_max=0.1)
        self.link.start()
        self.addCleanup(self.link.close)
        self.assertTrue(self.link.opened.wait(3))
        self.driver.start(self.link.emit)

    def _server(self) -> BridgeServer:
        server = BridgeServer(self.path, tick_seconds=0.05)
        server.start()
        self.addCleanup(server.close)
        return server

    def _client(self, recorder: _Recorder | None = None, **options) -> BridgeClient:
        recorder = recorder or _Recorder()
        client = BridgeClient(
            self.path,
            client_id=options.pop("client_id", "web"),
            on_console=recorder.console,
            on_interaction=recorder.interaction,
            on_connection=recorder.connection,
            reconnect_initial=0.05,
            reconnect_max=0.1,
            **options,
        )
        client.start()
        self.addCleanup(client.close)
        self.assertTrue(client.wait_ready(3))
        return client

    def _approval(self, ref: str = "rpc-1") -> None:
        self.driver.emit(
            FactKind.REQUEST_OPENED,
            {"provider_ref": ref, "kind": "approval", "summary": "请求使用工具 Bash",
             "expires_at": int(time.time() * 1000) + 60_000, "answerable": True},
        )

    def test_the_console_follows_snapshot_and_deltas(self) -> None:
        recorder = _Recorder()
        client = self._client(recorder)
        self.assertEqual(client.console().session(KEY)["activity"], "idle")

        self.driver.emit(FactKind.TURN_STARTED, {"turn_ref": "turn-1"})
        _wait(lambda: client.console().session(KEY)["activity"] == "working")

        self.assertTrue(recorder.changes[0].reset)
        self.assertFalse(recorder.changes[-1].reset)
        self.assertEqual(recorder.connections, [True])

    def test_a_pending_approval_survives_a_client_reconnect(self) -> None:
        self._approval()
        first = self._client()
        _wait(lambda: bool(first.console().approvals(KEY)))
        first.close()

        second = self._client(client_id="web-2")
        tickets = second.console().approvals(KEY)
        result = second.command("approve", KEY, {"approval_id": tickets[0]["approval_id"]})
        kind, payload = self.driver.performed.get(timeout=2)

        self.assertEqual(result.code, "dispatched")
        self.assertEqual((kind, payload["provider_ref"], payload["decision"]), (EffectKind.RESPOND, "rpc-1", "approve"))

    def test_the_console_resumes_from_its_cursor(self) -> None:
        recorder = _Recorder()
        client = self._client(recorder)
        client._sock.shutdown(socket.SHUT_RDWR)
        _wait(lambda: recorder.connections[-1:] == [False])
        self.driver.emit(FactKind.TURN_STARTED, {"turn_ref": "turn-1"})
        _wait(lambda: client.connected and client.console().session(KEY)["activity"] == "working")

        self.assertEqual([change.reset for change in recorder.changes], [True, False])

    def test_a_bridge_restart_resets_the_console(self) -> None:
        recorder = _Recorder()
        client = self._client(recorder)
        before = client.console().instance_id
        self.server.close()
        self.server = self._server()
        _wait(lambda: client.connected and client.console().instance_id != before and client.console().session(KEY))

        restarted = [change for change in recorder.changes if change.restarted]
        self.assertTrue(restarted and restarted[0].reset)

    def test_replayed_events_arrive_once(self) -> None:
        self.driver.emit(FactKind.MESSAGE, {"message_ref": "m1", "role": "assistant", "phase": "complete"}, {"text": "hello"})
        recorder = _Recorder()
        client = self._client(recorder, interaction=True)
        _wait(lambda: len(recorder.events) == 1)
        client._sock.shutdown(socket.SHUT_RDWR)
        _wait(lambda: recorder.connections[-2:] == [False, True])
        self.driver.emit(FactKind.MESSAGE, {"message_ref": "m2", "role": "assistant", "phase": "complete"}, {"text": "again"})
        _wait(lambda: len(recorder.events) == 2)
        time.sleep(0.2)

        texts = [(event["payload"]["text"], replay) for event, replay in recorder.events]
        self.assertEqual(texts, [("hello", True), ("again", False)])

    def test_commands_and_queries_answer_or_report_the_bridge_missing(self) -> None:
        self.driver.emit(FactKind.CATALOG, content={"catalog": {"model": "example-model"}})
        client = self._client()
        _wait(lambda: client.catalog(KEY)[0] == "ok")

        submitted = client.command("submit_prompt", KEY, {"text": "hello"}, command_id="c1")
        status, catalog = client.catalog(KEY)
        self.server.close()
        _wait(lambda: not client.connected)
        missing = client.command("interrupt", KEY)

        self.assertEqual((submitted.command_id, submitted.code, submitted.accepted), ("c1", "dispatched", True))
        self.assertEqual((status, catalog["model"]), ("ok", "example-model"))
        self.assertEqual((missing.code, missing.accepted), ("bridge_unavailable", False))

    def test_the_voice_tier_reads_no_interaction(self) -> None:
        with self.assertRaises(ValueError):
            BridgeClient(self.path, client_id="voice", tier=Tier.VOICE, interaction=True)
        voice = self._client(client_id="voice", tier=Tier.VOICE)

        self.assertEqual(voice.command("focus", KEY).code, "not_permitted")

    def test_the_cli_lists_sessions_and_focuses_one(self) -> None:
        self._approval()
        listed = self._run_cli(["--socket", str(self.path), "sessions"])
        focused = self._run_cli(["--socket", str(self.path), "focus", "--agent-id", "fake",
                                 "--session-id", "session-1", "--project-id", "demo"])

        self.assertIn("fake/session-1/demo", listed[1])
        self.assertIn("请求使用工具 Bash", listed[1])
        self.assertEqual(focused, (0, "已聚焦这个会话\n"))

    def _run_cli(self, argv: list[str]) -> tuple[int, str]:
        import contextlib
        import io

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = client_main(argv)
        return code, output.getvalue()


class TextTests(unittest.TestCase):
    def test_every_outcome_has_one_wording(self) -> None:
        self.assertEqual(result_text("undelivered"), "Agent 当前无法接收这个操作")
        self.assertEqual(result_text("something_new"), "something_new")
        self.assertEqual(activity_text({"lifecycle": "disconnected", "activity": "idle"}), "已断开")
        self.assertEqual(outcome_text({"status": "failed", "failure": "usage_limit"}), "这一轮失败：用量已到上限")
        self.assertEqual(outcome_text(None), "")


if __name__ == "__main__":
    unittest.main()
