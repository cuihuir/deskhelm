import unittest

from deskhelm_bridge.client import _voice_status_message


class ClientVoiceStatusTests(unittest.TestCase):
    def test_none_target_filter_allows_focused_mode_status_stream(self) -> None:
        event = {
            "agent_id": "codex",
            "session_id": "session-2",
            "project_id": "project-2",
            "source": "voice-gateway",
            "kind": "message",
            "payload": {
                "role": "system",
                "phase": "complete",
                "text": "fixed notification",
            },
        }

        self.assertEqual(
            _voice_status_message(
                event,
                agent_id=None,
                session_id=None,
                project_id=None,
            ),
            "fixed notification",
        )

    def test_surfaces_fixed_codex_failure_code_without_private_content(self) -> None:
        event = {
            "agent_id": "codex",
            "session_id": "session-1",
            "project_id": "project-1",
            "source": "codex-app-server",
            "kind": "task_failed",
            "payload": {
                "message": "private command and path",
                "error_code": "codex_control_failed",
            },
        }

        message = _voice_status_message(
            event,
            agent_id="codex",
            session_id="session-1",
            project_id="project-1",
        )

        self.assertEqual(
            message,
            "Codex 控制请求被拒绝（codex_control_failed）",
        )
        self.assertNotIn("private", message)

    def test_ignores_unknown_failures_and_other_targets(self) -> None:
        event = {
            "agent_id": "other",
            "session_id": "session-1",
            "project_id": "project-1",
            "source": "codex-app-server",
            "kind": "task_failed",
            "payload": {"error_code": "private_error"},
        }

        self.assertIsNone(
            _voice_status_message(
                event,
                agent_id="codex",
                session_id="session-1",
                project_id="project-1",
            )
        )


if __name__ == "__main__":
    unittest.main()
