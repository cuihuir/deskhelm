import json
from pathlib import Path
import unittest

from deskhelm_bridge.event import ProtocolError
from deskhelm_bridge.interaction import (
    ApprovalRequestPayload,
    InteractionEvent,
    InteractionKind,
    MessagePayload,
    MessagePhase,
    MessageRole,
    TaskPayload,
    ToolPayload,
    ToolPhase,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "protocol" / "interaction-v1"


class InteractionEventTests(unittest.TestCase):
    def test_protocol_fixtures_round_trip(self) -> None:
        for fixture_path in sorted(FIXTURE_DIR.glob("*.json")):
            with self.subTest(fixture=fixture_path.name):
                value = json.loads(fixture_path.read_text(encoding="utf-8"))
                event = InteractionEvent.from_dict(value)

                self.assertEqual(event.to_dict(), value)
                self.assertEqual(InteractionEvent.from_json(event.to_json()), event)

    def test_rejects_unknown_kind(self) -> None:
        value = self._fixture("message-delta.json")
        value["kind"] = "reasoning"

        with self.assertRaises(ProtocolError):
            InteractionEvent.from_dict(value)

    def test_rejects_missing_session_identity(self) -> None:
        value = self._fixture("message-delta.json")
        value["session_id"] = ""

        with self.assertRaisesRegex(ProtocolError, "session_id"):
            InteractionEvent.from_dict(value)

    def test_rejects_negative_or_boolean_sequence(self) -> None:
        value = self._fixture("message-delta.json")
        value["sequence"] = -1
        with self.assertRaisesRegex(ProtocolError, "sequence"):
            InteractionEvent.from_dict(value)

        value["sequence"] = True
        with self.assertRaisesRegex(ProtocolError, "sequence"):
            InteractionEvent.from_dict(value)

    def test_message_delta_requires_text_and_correlation(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "delta text"):
            MessagePayload(
                role=MessageRole.ASSISTANT,
                phase=MessagePhase.DELTA,
            )

        with self.assertRaisesRegex(ProtocolError, "correlation_id"):
            self._message_event(correlation_id="")

    def test_tool_output_requires_stream(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "stream"):
            ToolPayload(
                tool_call_id="tool-1",
                name="shell",
                phase=ToolPhase.OUTPUT,
                text="running",
            )

    def test_rejects_payload_for_wrong_kind(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "TaskPayload"):
            InteractionEvent(
                kind=InteractionKind.TASK_COMPLETED,
                agent_id="codex",
                session_id="session-1",
                project_id="deskhelm",
                source="test",
                source_version="1",
                sequence=1,
                correlation_id="",
                payload=MessagePayload(
                    role=MessageRole.ASSISTANT,
                    phase=MessagePhase.COMPLETE,
                    text="done",
                ),
            )

    def test_approval_must_expire_after_event(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "after occurred_at"):
            InteractionEvent(
                kind=InteractionKind.APPROVAL_REQUEST,
                agent_id="codex",
                session_id="session-1",
                project_id="deskhelm",
                source="test",
                source_version="1",
                sequence=1,
                occurred_at=100,
                correlation_id="approval-1",
                payload=ApprovalRequestPayload(
                    request_id="approval-1",
                    summary="Run tests",
                    expires_at=100,
                ),
            )

    def test_tool_and_approval_correlation_ids_must_match_payload(self) -> None:
        tool_value = self._fixture("tool-complete.json")
        tool_value["correlation_id"] = "different-tool"
        with self.assertRaisesRegex(ProtocolError, "tool correlation_id"):
            InteractionEvent.from_dict(tool_value)

        approval_value = self._fixture("approval-request.json")
        approval_value["correlation_id"] = "different-request"
        with self.assertRaisesRegex(ProtocolError, "approval correlation_id"):
            InteractionEvent.from_dict(approval_value)

    def test_wire_event_requires_positive_occurred_at(self) -> None:
        value = self._fixture("message-delta.json")
        value["occurred_at"] = 0

        with self.assertRaisesRegex(ProtocolError, "occurred_at"):
            InteractionEvent.from_dict(value)

    def test_failed_task_requires_message(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "failed task message"):
            InteractionEvent(
                kind=InteractionKind.TASK_FAILED,
                agent_id="codex",
                session_id="session-1",
                project_id="deskhelm",
                source="test",
                source_version="1",
                sequence=1,
                payload=TaskPayload(),
            )

    def test_completed_task_rejects_error_code(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "completed task"):
            InteractionEvent(
                kind=InteractionKind.TASK_COMPLETED,
                agent_id="codex",
                session_id="session-1",
                project_id="deskhelm",
                source="test",
                source_version="1",
                sequence=1,
                payload=TaskPayload(message="done", error_code="unexpected"),
            )

    @staticmethod
    def _fixture(name: str) -> dict[str, object]:
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))

    @staticmethod
    def _message_event(correlation_id: str) -> InteractionEvent:
        return InteractionEvent(
            kind=InteractionKind.MESSAGE,
            agent_id="codex",
            session_id="session-1",
            project_id="deskhelm",
            source="test",
            source_version="1",
            sequence=1,
            correlation_id=correlation_id,
            payload=MessagePayload(
                role=MessageRole.ASSISTANT,
                phase=MessagePhase.DELTA,
                text="hello",
            ),
        )


if __name__ == "__main__":
    unittest.main()
