from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch
import time
import unittest

from deskhelm_bridge.dashboard import _DashboardState, _read_interaction, _render
from deskhelm_bridge.dashboard import _handle_key
from deskhelm_bridge.control_result import ControlResult, ControlResultCode, ControlResultStatus
from deskhelm_bridge.event import AgentEvent, AgentState
from deskhelm_bridge.transport import encode_frame


class DashboardTests(unittest.TestCase):
    def test_approval_key_copies_exact_pending_request_metadata(self) -> None:
        expires_at = int(time.time() * 1000) + 30_000
        state = _DashboardState(
            sessions=[
                {
                    "agent_id": "codex",
                    "session_id": "session-1",
                    "project_id": "deskhelm",
                    "slot": 0,
                    "state": "active",
                }
            ],
        )
        target = ("codex", "session-1", "deskhelm")
        state.approvals[target] = ("approval-1", "run tests", expires_at)
        result = ControlResult(
            command_id="command-1",
            status=ControlResultStatus.ACCEPTED,
            code=ControlResultCode.DISPATCHED,
            processed_at=int(time.time() * 1000),
        )
        with patch("deskhelm_bridge.dashboard.send_control_command", return_value=result) as send:
            _handle_key(b"a", state, Path("/tmp/bridge.sock"), "dashboard")
        command = send.call_args.args[0]
        self.assertEqual(command.kind.value, "approve")
        self.assertEqual(command.payload.request_id, "approval-1")
        self.assertEqual(command.payload.summary, "run tests")
        self.assertEqual(command.expires_at, expires_at)
        self.assertNotIn(target, state.approvals)

    def test_render_keeps_cursor_and_focus_distinct_and_shows_approval(self) -> None:
        state = _DashboardState(
            events={0: AgentEvent("codex", 0, AgentState.WAITING_APPROVAL, "backend")},
            sessions=[
                {
                    "agent_id": "codex",
                    "session_id": "session-1",
                    "project_id": "deskhelm",
                    "slot": 0,
                    "state": "active",
                }
            ],
            focused=None,
        )
        state.approvals[("codex", "session-1", "deskhelm")] = (
            "approval-1",
            "run tests",
            9_999_999_999_999,
        )
        output = StringIO()
        with patch("deskhelm_bridge.dashboard.sys.stdout", output):
            _render(state)
        rendered = output.getvalue()
        self.assertIn(">  backend", rendered)
        self.assertIn("approval pending", rendered)
        self.assertIn("[display only]", rendered)

    def test_interaction_approval_is_retained_for_selected_identity(self) -> None:
        frame = {
            "protocol_version": 1,
            "message_type": "interaction_update",
            "stream_id": "stream-1",
            "subscription_id": "subscription-1",
            "sequence": 1,
            "event": {
                "protocol_version": 1,
                "message_type": "interaction_event",
                "event_id": "event-1",
                "kind": "approval_request",
                "agent_id": "codex",
                "session_id": "session-1",
                "project_id": "deskhelm",
                "source": "test",
                "source_version": "1",
                "sequence": 1,
                "occurred_at": 1_000,
                "correlation_id": "approval-1",
                "payload": {
                    "request_id": "approval-1",
                    "summary": "run tests",
                    "expires_at": 2_000,
                },
            },
        }
        state = _DashboardState()
        _read_interaction(BytesIO(encode_frame(frame)), state)
        self.assertEqual(
            state.approvals[("codex", "session-1", "deskhelm")],
            ("approval-1", "run tests", 2_000),
        )


if __name__ == "__main__":
    unittest.main()
