import json
from pathlib import Path
import unittest


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "adapters" / "codex-exec-json"


class CodexExecFixtureTests(unittest.TestCase):
    def test_manifest_records_provenance_and_runtime_version(self) -> None:
        manifest = json.loads(
            (FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["fixture_schema_version"], 1)
        self.assertEqual(manifest["adapter_id"], "deskhelm-codex-exec")
        self.assertEqual(manifest["format"], "jsonl")
        self.assertEqual(manifest["runtime_name"], "codex-cli")
        self.assertEqual(manifest["locally_inspected_runtime_version"], "0.147.0")
        self.assertEqual(manifest["retrieved_at"], "2026-08-18")

        official = next(
            fixture
            for fixture in manifest["fixtures"]
            if fixture["file"] == "official-basic.jsonl"
        )
        self.assertEqual(
            official["source_type"], "official_documentation_example"
        )
        self.assertEqual(
            official["source_url"],
            "https://learn.chatgpt.com/docs/non-interactive-mode",
        )
        self.assertFalse(official["produced_locally"])

    def test_valid_jsonl_fixtures_are_line_delimited_objects(self) -> None:
        for name in (
            "official-basic.jsonl",
            "synthetic-turn-failed.jsonl",
            "synthetic-unknown.jsonl",
        ):
            with self.subTest(fixture=name):
                lines = (FIXTURE_DIR / name).read_text(encoding="utf-8").splitlines()
                self.assertTrue(lines)
                values = [json.loads(line) for line in lines]
                self.assertTrue(all(isinstance(value, dict) for value in values))
                self.assertTrue(all(isinstance(value.get("type"), str) for value in values))

    def test_malformed_fixture_is_detected(self) -> None:
        line = (FIXTURE_DIR / "malformed.jsonl").read_text(encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            json.loads(line)


if __name__ == "__main__":
    unittest.main()
