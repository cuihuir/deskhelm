import json
from pathlib import Path
import unittest

from deskhelm_bridge.control import (
    ApprovalDecisionPayload,
    ControlCommand,
    ControlKind,
    SpeakPayload,
    SubmitPromptPayload,
)
from deskhelm_bridge.event import ProtocolError


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "protocol" / "control-v1"


class ControlCommandTests(unittest.TestCase):
    def test_protocol_fixtures_round_trip(self) -> None:
        for fixture_path in sorted(FIXTURE_DIR.glob("*.json")):
            with self.subTest(fixture=fixture_path.name):
                value = json.loads(fixture_path.read_text(encoding="utf-8"))
                command = ControlCommand.from_dict(value)

                self.assertEqual(command.to_dict(), value)
                self.assertEqual(ControlCommand.from_json(command.to_json()), command)

    def test_rejects_unknown_kind_and_version(self) -> None:
        value = self._fixture("focus.json")
        value["kind"] = "mute"
        with self.assertRaises(ProtocolError):
            ControlCommand.from_dict(value)

        value = self._fixture("focus.json")
        value["protocol_version"] = 2
        with self.assertRaisesRegex(ProtocolError, "protocol_version"):
            ControlCommand.from_dict(value)

    def test_requires_complete_target_identity(self) -> None:
        for field_name in ("agent_id", "session_id", "project_id"):
            with self.subTest(field=field_name):
                value = self._fixture("focus.json")
                value[field_name] = ""
                with self.assertRaisesRegex(ProtocolError, field_name):
                    ControlCommand.from_dict(value)

    def test_requires_idempotency_key_and_issuer(self) -> None:
        value = self._fixture("submit-prompt.json")
        value["idempotency_key"] = ""
        with self.assertRaisesRegex(ProtocolError, "idempotency_key"):
            ControlCommand.from_dict(value)

        value = self._fixture("submit-prompt.json")
        value["issued_by"] = ""
        with self.assertRaisesRegex(ProtocolError, "issued_by"):
            ControlCommand.from_dict(value)

    def test_expiry_must_follow_issue_time(self) -> None:
        value = self._fixture("focus.json")
        value["expires_at"] = value["issued_at"]

        with self.assertRaisesRegex(ProtocolError, "after issued_at"):
            ControlCommand.from_dict(value)

    def test_is_expired_at_deadline(self) -> None:
        command = ControlCommand.from_dict(self._fixture("focus.json"))

        self.assertFalse(command.is_expired(now_ms=command.expires_at - 1))
        self.assertTrue(command.is_expired(now_ms=command.expires_at))

    def test_approval_copies_request_expiry_exactly(self) -> None:
        value = self._fixture("approve.json")
        value["payload"]["request_expires_at"] += 1

        with self.assertRaisesRegex(ProtocolError, "request_expires_at"):
            ControlCommand.from_dict(value)

    def test_approval_fixture_matches_interaction_request(self) -> None:
        command = self._fixture("approve.json")
        interaction_path = (
            FIXTURE_DIR.parent / "interaction-v1" / "approval-request.json"
        )
        event = json.loads(interaction_path.read_text(encoding="utf-8"))

        self.assertEqual(command["agent_id"], event["agent_id"])
        self.assertEqual(command["session_id"], event["session_id"])
        self.assertEqual(command["project_id"], event["project_id"])
        self.assertEqual(command["payload"]["request_id"], event["correlation_id"])
        self.assertEqual(command["payload"]["summary"], event["payload"]["summary"])
        self.assertEqual(
            command["payload"]["request_expires_at"],
            event["payload"]["expires_at"],
        )

    def test_approval_requires_request_and_summary(self) -> None:
        for field_name in ("request_id", "summary"):
            with self.subTest(field=field_name):
                value = self._fixture("reject.json")
                value["payload"][field_name] = ""
                with self.assertRaisesRegex(ProtocolError, field_name):
                    ControlCommand.from_dict(value)

    def test_prompt_and_speech_require_non_empty_text(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "prompt text"):
            SubmitPromptPayload(text="")
        with self.assertRaisesRegex(ProtocolError, "speech text"):
            SpeakPayload(text="")

    def test_speech_interruptible_must_be_boolean(self) -> None:
        value = self._fixture("speak.json")
        value["payload"]["interruptible"] = 1

        with self.assertRaisesRegex(ProtocolError, "boolean"):
            ControlCommand.from_dict(value)

    def test_rejects_payload_for_wrong_kind(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "FocusPayload"):
            ControlCommand(
                kind=ControlKind.FOCUS,
                agent_id="codex",
                session_id="session-1",
                project_id="deskhelm",
                issued_by="test",
                issued_at=100,
                expires_at=200,
                idempotency_key="focus-1",
                payload=SubmitPromptPayload(text="hello"),
            )

    def test_approval_payload_rejects_non_positive_expiry(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "request_expires_at"):
            ApprovalDecisionPayload(
                request_id="approval-1",
                summary="Run tests",
                request_expires_at=0,
            )

    @staticmethod
    def _fixture(name: str) -> dict[str, object]:
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
