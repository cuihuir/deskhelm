import json
from pathlib import Path
import unittest

from deskhelm_bridge.control_result import (
    ControlResult,
    ControlResultCode,
    ControlResultStatus,
)
from deskhelm_bridge.event import ProtocolError


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "protocol" / "control-result-v1"


class ControlResultTests(unittest.TestCase):
    def test_protocol_fixtures_round_trip(self) -> None:
        for fixture_path in sorted(FIXTURE_DIR.glob("*.json")):
            with self.subTest(fixture=fixture_path.name):
                value = json.loads(fixture_path.read_text(encoding="utf-8"))
                result = ControlResult.from_dict(value)

                self.assertEqual(result.to_dict(), value)
                self.assertEqual(ControlResult.from_json(result.to_json()), result)

    def test_status_must_match_result_code(self) -> None:
        value = self._fixture("accepted.json")
        value["status"] = "rejected"

        with self.assertRaisesRegex(ProtocolError, "status does not match"):
            ControlResult.from_dict(value)

    def test_duplicate_must_be_boolean(self) -> None:
        value = self._fixture("rejected.json")
        value["duplicate"] = 1

        with self.assertRaisesRegex(ProtocolError, "boolean"):
            ControlResult.from_dict(value)

    def test_protocol_version_must_not_be_boolean(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "integer"):
            ControlResult(
                command_id="command-1",
                status=ControlResultStatus.ACCEPTED,
                code=ControlResultCode.FOCUSED,
                processed_at=1,
                protocol_version=True,
            )

    @staticmethod
    def _fixture(name: str) -> dict[str, object]:
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
