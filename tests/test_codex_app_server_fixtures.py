import json
from pathlib import Path
import unittest


FIXTURE_DIRECTORY = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "adapters"
    / "codex-app-server-v2"
)
MAX_FRAME_BYTES = 1 << 20


class CodexAppServerFixtureTests(unittest.TestCase):
    def test_manifest_names_complete_bounded_json_rpc_objects(self) -> None:
        manifest = json.loads(
            (FIXTURE_DIRECTORY / "manifest.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["locally_inspected_runtime_version"], "0.150.1")
        self.assertEqual(
            manifest["official_source_url"],
            "https://learn.chatgpt.com/docs/app-server",
        )
        for record in manifest["fixtures"]:
            raw = (FIXTURE_DIRECTORY / record["file"]).read_bytes()
            self.assertLessEqual(len(raw), MAX_FRAME_BYTES)
            value = json.loads(raw)
            self.assertIsInstance(value, dict)
            self.assertTrue("id" in value or "method" in value)


if __name__ == "__main__":
    unittest.main()
