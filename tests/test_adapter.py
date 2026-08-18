import json
from pathlib import Path
import unittest

from deskhelm_bridge.adapter import AdapterSessionEvent, AdapterSessionResult
from deskhelm_bridge.event import ProtocolError


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "protocol" / "adapter-session-v1"


class AdapterSessionProtocolTests(unittest.TestCase):
    def test_protocol_fixtures_round_trip(self) -> None:
        for name in ("register.json", "disconnect.json", "release.json"):
            with self.subTest(fixture=name):
                value = self._fixture(name)
                event = AdapterSessionEvent.from_dict(value)
                self.assertEqual(event.to_dict(), value)
                self.assertEqual(AdapterSessionEvent.from_json(event.to_json()), event)

        for name in ("register-result.json", "release-result.json"):
            with self.subTest(fixture=name):
                value = self._fixture(name)
                result = AdapterSessionResult.from_dict(value)
                self.assertEqual(result.to_dict(), value)
                self.assertEqual(
                    AdapterSessionResult.from_json(result.to_json()), result
                )

    def test_rejects_unknown_or_duplicate_capabilities(self) -> None:
        value = self._fixture("register.json")
        value["capabilities"] = ["unknown"]
        with self.assertRaisesRegex(ProtocolError, "unknown"):
            AdapterSessionEvent.from_dict(value)

        value = self._fixture("register.json")
        value["capabilities"].append(value["capabilities"][0])
        with self.assertRaisesRegex(ProtocolError, "duplicates"):
            AdapterSessionEvent.from_dict(value)

    def test_preferred_slot_is_register_only(self) -> None:
        value = self._fixture("disconnect.json")
        value["preferred_slot"] = 1

        with self.assertRaisesRegex(ProtocolError, "register"):
            AdapterSessionEvent.from_dict(value)

    def test_release_result_must_not_retain_slot(self) -> None:
        value = self._fixture("release-result.json")
        value["slot"] = 1

        with self.assertRaisesRegex(ProtocolError, "must be null"):
            AdapterSessionResult.from_dict(value)

    @staticmethod
    def _fixture(name: str) -> dict[str, object]:
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
