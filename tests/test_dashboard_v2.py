from __future__ import annotations

import io
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from deskhelm_client import BridgeClient  # noqa: E402
from deskhelm_core.server import BridgeServer  # noqa: E402
from deskhelm_dashboard import Dashboard, run_dashboard  # noqa: E402
from deskhelm_dashboard.app import item_line  # noqa: E402
from deskhelm_host import BridgeLink  # noqa: E402
from deskhelm_protocol import EffectKind, FactKind, SessionKey  # noqa: E402
from helpers.v2 import FakeDriver  # noqa: E402


KEY = SessionKey("fake", "session-1", "demo")


def _wait(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met")
        time.sleep(0.02)


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "bridge.sock"
        server = BridgeServer(path, tick_seconds=0.05)
        server.start()
        self.addCleanup(server.close)
        self.driver = FakeDriver()
        link = BridgeLink(path, KEY, self.driver, client_id="host-test", reconnect_initial=0.05, reconnect_max=0.1)
        link.start()
        self.addCleanup(link.close)
        self.assertTrue(link.opened.wait(3))
        self.driver.start(link.emit)
        holder: dict[str, Dashboard] = {}
        self.client = BridgeClient(
            path,
            client_id="dashboard",
            interaction=True,
            on_console=lambda change: holder["dashboard"].on_console(change),
            on_interaction=lambda event, replay: holder["dashboard"].on_interaction(event, replay),
            on_connection=lambda connected: holder["dashboard"].on_connection(connected),
        )
        self.dashboard = holder["dashboard"] = Dashboard(self.client)
        self.client.start()
        self.addCleanup(self.client.close)
        self.assertTrue(self.client.wait_ready(3))

    def _approval(self) -> None:
        self.driver.emit(
            FactKind.REQUEST_OPENED,
            {"provider_ref": "rpc-1", "kind": "approval", "summary": "请求使用工具 Bash",
             "expires_at": int(time.time() * 1000) + 60_000, "answerable": True},
        )
        _wait(lambda: bool(self.client.console().approvals(KEY)))

    def test_a_pending_approval_is_shown_and_approved_from_the_keys(self) -> None:
        self._approval()
        screen = "\n".join(self.dashboard.render())
        self.assertIn("待批准 1", screen)
        self.assertIn("请求使用工具 Bash", screen)
        self.assertIn("可在这里处理", screen)

        self.assertTrue(self.dashboard.handle_key(b"a"))
        kind, payload = self.driver.performed.get(timeout=2)

        self.assertEqual((kind, payload["provider_ref"], payload["decision"]), (EffectKind.RESPOND, "rpc-1", "approve"))
        self.assertEqual(self.dashboard.state.notice, "已交给 Agent")

    def test_a_decision_without_a_pending_approval_is_not_sent(self) -> None:
        self.dashboard.handle_key(b"r")

        self.assertEqual(self.dashboard.state.notice, "这个会话没有等待批准的请求")
        self.assertTrue(self.driver.performed.empty())

    def test_focus_and_activity_appear_in_the_list(self) -> None:
        self.dashboard.handle_key(b"f")
        _wait(lambda: self.client.console().focus == KEY)
        self.driver.emit(FactKind.MESSAGE, {"message_ref": "m1", "role": "assistant", "phase": "complete"}, {"text": "hello\nthere"})
        _wait(lambda: "fake：hello there" in "\n".join(self.dashboard.render()))
        screen = self.dashboard.render()

        self.assertTrue(any(line.startswith(">*") and "空闲" in line for line in screen))
        self.assertEqual(self.dashboard.state.notice, "已聚焦这个会话")

    def test_recent_activity_shows_items_not_stream_fragments(self) -> None:
        for phase, piece in (("delta", "Hel"), ("delta", "lo"), ("complete", "Hello.")):
            self.driver.emit(FactKind.MESSAGE, {"message_ref": "m1", "role": "assistant", "phase": phase}, {"text": piece})
        self.driver.emit(FactKind.TOOL, {"tool_ref": "t1", "label": "command", "phase": "start"},
                         {"text": "uv run pytest"})
        self.driver.emit(FactKind.TOOL, {"tool_ref": "t1", "label": "command", "phase": "complete", "exit_code": 2, "status": "failed"})
        _wait(lambda: "退出码 2" in "\n".join(self.dashboard.render()))
        recent = self.dashboard.render()
        recent = recent[recent.index("  最近活动：") + 1:]

        self.assertEqual([line.strip() for line in recent], ["fake：Hello.", "命令 uv run pytest · 退出码 2"])

    def test_a_stopped_tool_reads_as_stopped(self) -> None:
        line = item_line({"type": "tool", "kind": "command", "title": "make", "status": "stopped", "exit_code": None}, "Codex")

        self.assertEqual(line, "命令 make · 已停止")
        failed = item_line({"type": "tool", "kind": "mcp", "title": "github", "status": "failed", "exit_code": None}, "Codex")
        self.assertEqual(failed, "MCP github · 失败")
        approved = item_line({"type": "approval", "summary": "Run git push", "resolution": "deskhelm",
                              "decision": "approve", "uncertain": True}, "Codex")
        self.assertEqual(approved, "批准请求：Run git push · 已批准，结果未知")
        self.assertEqual(item_line({"type": "approval", "summary": "Edit", "resolution": ""}, "Codex"), "批准请求：Edit")
        declined = item_line({"type": "tool", "kind": "command", "title": "rm -rf build", "status": "declined",
                              "exit_code": None}, "Codex")
        self.assertEqual(declined, "命令 rm -rf build · 已拒绝")
        detailed = item_line({"type": "approval", "summary": "请求使用工具 Bash", "detail": "git push\n  --force",
                              "resolution": "deskhelm", "decision": "reject"}, "Claude")
        self.assertEqual(detailed, "批准请求：请求使用工具 Bash：git push --force · 已拒绝")

    def test_a_lost_bridge_is_a_notice_and_q_quits(self) -> None:
        self.dashboard.on_connection(False)

        self.assertEqual(self.dashboard.state.notice, "Bridge 暂时不可用")
        self.assertFalse(self.dashboard.handle_key(b"q"))

    def test_a_terminal_is_required(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO()), mock.patch.object(sys, "stderr", stderr):
            code = run_dashboard(Path("/tmp/deskhelm-unused.sock"))

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
