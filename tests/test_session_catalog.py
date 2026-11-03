from __future__ import annotations

import json
from pathlib import Path
import unittest

from deskhelm_bridge.control import ControlCommand
from deskhelm_bridge.event import ProtocolError
from deskhelm_bridge.session_catalog import normalize_catalog, resolve_session_catalog


FIXTURE = (
    Path(__file__).parent / "fixtures" / "protocol" / "control-v1" / "configure-session.json"
)


class SessionCatalogTests(unittest.TestCase):
    def test_normalizer_drops_private_fields_and_clips_labels(self) -> None:
        long_name = "n" * 300
        catalog = normalize_catalog(
            {
                "notice": "",
                "model": "gpt-6.1-sol",
                "reasoning_effort": "medium",
                "cwd": "/tmp/private",
                "models": [
                    {
                        "id": "gpt-6.1-sol",
                        "name": long_name,
                        "is_default": True,
                        "default_effort": "medium",
                        "hidden": True,
                        "efforts": [
                            {"id": "medium", "description": "balanced\nwork"},
                            {"id": "bad effort", "description": "no"},
                        ],
                    }
                ],
                "mcp_servers": [
                    {
                        "name": "docs",
                        "status": "connected",
                        "auth": "oAuth",
                        "tools": ["search", {"name": "nope"}],
                        "toolsError": "private tool error",
                        "httpOrigin": "https://example.invalid",
                    }
                ],
                "skills": [
                    {
                        "name": "review",
                        "description": "Review a change",
                        "scope": "repo",
                        "enabled": True,
                        "path": "/home/secret/skills/review/SKILL.md",
                    }
                ],
            }
        )

        self.assertTrue(catalog["available"])
        self.assertEqual(catalog["model"], "gpt-6.1-sol")
        self.assertEqual(len(catalog["models"][0]["name"]), 128)
        self.assertTrue(catalog["models"][0]["name"].endswith("…"))
        self.assertEqual(catalog["models"][0]["efforts"], [
            {"id": "medium", "description": "balanced work"},
        ])
        self.assertEqual(catalog["mcp_servers"][0]["tools"], ["search"])
        self.assertNotIn("path", catalog["skills"][0])
        blob = json.dumps(catalog)
        self.assertNotIn("/tmp/private", blob)
        self.assertNotIn("SKILL.md", blob)
        self.assertNotIn("private tool error", blob)
        self.assertNotIn("example.invalid", blob)
        self.assertNotIn("cwd", catalog)
        self.assertEqual(catalog["agent_capabilities"], [])
        declared = normalize_catalog(
            {
                "notice": "",
                "agent_capabilities": [
                    "interrupt_receipt_v1",
                    "interrupt_receipt_v1",
                    "bad path",
                    "/tmp/private",
                    "a" * 200,
                ],
            }
        )
        self.assertEqual(declared["agent_capabilities"], ["interrupt_receipt_v1"])
        self.assertFalse(declared["truncated"])

    def test_unknown_notice_and_over_cap_lists_fail_closed(self) -> None:
        self.assertEqual(
            normalize_catalog({"notice": "show-the-prompt"})["notice"],
            "catalog_failed",
        )
        self.assertFalse(normalize_catalog("nope")["available"])
        models = [
            {"id": f"model-{index}", "name": f"Model {index}", "efforts": []}
            for index in range(41)
        ]
        catalog = normalize_catalog({"notice": "", "models": models})
        self.assertEqual(len(catalog["models"]), 40)
        self.assertTrue(catalog["truncated"])
        self.assertEqual(
            normalize_catalog(
                {
                    "notice": "",
                    "model": "has space",
                    "models_notice": "raw vendor text",
                    "mcp_servers": [
                        {"name": "docs", "status": "exploded", "auth": "cookie"}
                    ],
                    "skills": [{"name": "review", "scope": "workspace", "enabled": 1}],
                }
            )["model"],
            "",
        )
        normalized = normalize_catalog(
            {
                "notice": "",
                "models_notice": "raw vendor text",
                "mcp_servers": [
                    {"name": "docs", "status": "exploded", "auth": "cookie"}
                ],
                "skills": [{"name": "review", "scope": "workspace", "enabled": 1}],
            }
        )
        self.assertEqual(normalized["models_notice"], "unavailable")
        self.assertEqual(normalized["mcp_servers"][0]["status"], "unknown")
        self.assertEqual(normalized["mcp_servers"][0]["auth"], "")
        self.assertEqual(normalized["skills"][0]["scope"], "unknown")
        self.assertFalse(normalized["skills"][0]["enabled"])

    def test_session_state_short_circuits_before_a_provider_body(self) -> None:
        missing = resolve_session_catalog(
            found=False, active=False, raw={"notice": ""}, failed=False
        )
        inactive = resolve_session_catalog(
            found=True, active=False, raw={"notice": ""}, failed=False
        )
        unavailable = resolve_session_catalog(
            found=True, active=True, raw=None, failed=False
        )
        failed = resolve_session_catalog(
            found=True, active=True, raw={"notice": ""}, failed=True
        )

        self.assertEqual(missing["notice"], "target_not_found")
        self.assertEqual(inactive["notice"], "target_inactive")
        self.assertEqual(unavailable["notice"], "catalog_unavailable")
        self.assertEqual(failed["notice"], "catalog_failed")
        for body in (missing, inactive, unavailable, failed):
            self.assertFalse(body["available"])
            self.assertEqual(body["models_notice"], "unavailable")

    def test_configure_session_null_clears_and_an_empty_payload_is_rejected(self) -> None:
        value = json.loads(FIXTURE.read_text(encoding="utf-8"))
        value["payload"] = {"model": None, "reasoning_effort": "low"}
        command = ControlCommand.from_dict(value)

        self.assertTrue(command.payload.model_set)
        self.assertEqual(command.payload.model, "")
        self.assertEqual(command.payload.reasoning_effort, "low")
        self.assertIsNone(command.to_dict()["payload"]["model"])
        self.assertEqual(command.to_dict()["payload"]["reasoning_effort"], "low")

        value["payload"] = {}
        with self.assertRaises(ProtocolError):
            ControlCommand.from_dict(value)
        value["payload"] = {"model": "bad model"}
        with self.assertRaises(ProtocolError):
            ControlCommand.from_dict(value)


if __name__ == "__main__":
    unittest.main()
