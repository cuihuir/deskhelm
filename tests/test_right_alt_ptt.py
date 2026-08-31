from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deskhelm_bridge.cli import ptt_command
from deskhelm_bridge.control import ControlKind
from deskhelm_bridge.ptt import RIGHT_ALT_KEY, RightAltPttController


class RightAltPttControllerTests(unittest.TestCase):
    def test_pairs_one_right_alt_press_and_release(self) -> None:
        commands = []

        def dispatch(command):
            commands.append(command)
            return True

        controller = RightAltPttController("agent", "session", "project", dispatch)

        self.assertFalse(controller.observe("KEY_A", True))
        self.assertTrue(controller.observe(RIGHT_ALT_KEY, True))
        self.assertFalse(controller.observe(RIGHT_ALT_KEY, True))
        self.assertTrue(controller.observe(RIGHT_ALT_KEY, False))
        self.assertFalse(controller.observe(RIGHT_ALT_KEY, False))

        self.assertEqual([command.kind for command in commands], [ControlKind.PRESS_PTT, ControlKind.RELEASE_PTT])
        self.assertEqual(commands[1].payload.press_command_id, commands[0].command_id)

    def test_failed_press_does_not_create_a_release(self) -> None:
        commands = []
        controller = RightAltPttController(
            "agent", "session", "project", lambda command: commands.append(command) or False
        )

        self.assertFalse(controller.observe(RIGHT_ALT_KEY, True))
        self.assertFalse(controller.observe(RIGHT_ALT_KEY, False))
        self.assertEqual(len(commands), 1)

    def test_cli_ctrl_c_stops_without_a_traceback(self) -> None:
        args = SimpleNamespace(
            socket=Path("/tmp/deskhelm-test.sock"),
            input_device=Path("/dev/input/test"),
            agent_id="agent",
            session_id="session",
            project_id="project",
            client_id="deskhelm-test-ptt",
        )
        with (
            patch(
                "deskhelm_bridge.client.iter_voice_status_messages",
                return_value=iter(()),
            ),
            patch(
                "deskhelm_bridge.evdev_ptt.run_right_alt_listener",
                side_effect=KeyboardInterrupt,
            ),
        ):
            ptt_command(args)


if __name__ == "__main__":
    unittest.main()
