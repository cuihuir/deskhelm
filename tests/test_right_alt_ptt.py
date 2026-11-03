from pathlib import Path
import fcntl
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

from deskhelm_bridge.cli import build_parser, focus_command, ptt_command
from deskhelm_bridge.control import ControlKind
from deskhelm_bridge.control_result import (
    ControlResult,
    ControlResultCode,
    ControlResultStatus,
)
from deskhelm_bridge.evdev_ptt import run_right_alt_listener
from deskhelm_bridge.ptt import RIGHT_ALT_KEY, RightAltPttController


class RightAltPttControllerTests(unittest.TestCase):
    def test_focused_cli_does_not_require_static_target(self) -> None:
        args = build_parser().parse_args(
            [
                "ptt",
                "--input-device",
                "/dev/input/test",
                "--focused",
            ]
        )

        self.assertIsNone(args.agent_id)
        self.assertIsNone(args.session_id)
        self.assertIsNone(args.project_id)

    def test_focus_cli_updates_bridge_focus(self) -> None:
        args = build_parser().parse_args(
            [
                "focus",
                "--socket",
                "/tmp/deskhelm-test.sock",
                "--agent-id",
                "agent",
                "--session-id",
                "session",
                "--project-id",
                "project",
                "--client-id",
                "focus-ui",
            ]
        )
        result = ControlResult(
            command_id="focus-command",
            status=ControlResultStatus.ACCEPTED,
            code=ControlResultCode.FOCUSED,
            processed_at=1,
        )
        output = StringIO()
        with patch(
            "deskhelm_bridge.cli.send_control_command", return_value=result
        ) as send, redirect_stdout(output):
            focus_command(args)

        command = send.call_args.args[0]
        self.assertIs(command.kind, ControlKind.FOCUS)
        self.assertEqual(command.agent_id, "agent")
        self.assertEqual(command.session_id, "session")
        self.assertEqual(command.project_id, "project")
        self.assertEqual(send.call_args.kwargs["client_id"], "focus-ui")
        self.assertIn('"code":"focused"', output.getvalue())

    def test_focused_target_is_resolved_on_press_and_locked_until_release(self) -> None:
        commands = []
        current = [("agent-a", "session-a", "project-a")]
        controller = RightAltPttController(
            "", "", "", lambda command: commands.append(command) or True,
            target_resolver=lambda: current[0],
        )

        self.assertTrue(controller.observe(RIGHT_ALT_KEY, True))
        current[0] = ("agent-b", "session-b", "project-b")
        self.assertTrue(controller.observe(RIGHT_ALT_KEY, False))

        self.assertEqual(
            (commands[0].agent_id, commands[0].session_id, commands[0].project_id),
            ("agent-a", "session-a", "project-a"),
        )
        self.assertEqual(
            (commands[1].agent_id, commands[1].session_id, commands[1].project_id),
            ("agent-a", "session-a", "project-a"),
        )

    def test_focused_target_without_focus_does_not_dispatch(self) -> None:
        commands = []
        controller = RightAltPttController(
            "", "", "", lambda command: commands.append(command) or True,
            target_resolver=lambda: None,
        )

        self.assertFalse(controller.observe(RIGHT_ALT_KEY, True))
        self.assertFalse(controller.observe(RIGHT_ALT_KEY, False))
        self.assertEqual(commands, [])

    def test_listener_reads_right_alt_without_grabbing(self) -> None:
        commands = []
        created = []

        class FakeDevice:
            def __init__(self, path):
                self.path = path
                self.closed = False
                created.append(self)

            def grab(self):
                raise AssertionError("PTT must not grab the keyboard")

            def read_loop(self):
                return (
                    SimpleNamespace(type=1, code=30, value=1),
                    SimpleNamespace(type=1, code=RIGHT_ALT_KEY, value=2),
                    SimpleNamespace(type=1, code=RIGHT_ALT_KEY, value=1),
                    SimpleNamespace(type=1, code=RIGHT_ALT_KEY, value=0),
                    SimpleNamespace(type=0, code=RIGHT_ALT_KEY, value=1),
                )

            def close(self):
                self.closed = True

        # The listener only needs these constants; the real evdev module is
        # optional in the dependency-minimal test environment.
        fake_evdev = SimpleNamespace(
            InputDevice=FakeDevice,
            ecodes=SimpleNamespace(EV_KEY=1, KEY_RIGHTALT=RIGHT_ALT_KEY),
        )
        controller = RightAltPttController(
            "agent",
            "session",
            "project",
            lambda command: commands.append(command) or True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.dict(sys.modules, {"evdev": fake_evdev}),
                patch(
                    "deskhelm_bridge.evdev_ptt.deskhelm_runtime_directory",
                    return_value=Path(temporary),
                ),
            ):
                run_right_alt_listener(Path("/dev/input/test"), controller)

        self.assertEqual(
            [command.kind for command in commands],
            [ControlKind.PRESS_PTT, ControlKind.RELEASE_PTT],
        )
        self.assertTrue(created[0].closed)
        self.assertFalse(hasattr(created[0], "grabbed"))

    def test_listener_rejects_second_owner(self) -> None:
        created = []

        class IdleDevice:
            def __init__(self, path):
                self.path = path
                self.closed = False
                created.append(self)

            def grab(self):
                raise AssertionError("PTT must not grab the keyboard")

            def read_loop(self):
                raise AssertionError("a busy listener must not read keys")

            def close(self):
                self.closed = True

        fake_evdev = SimpleNamespace(
            InputDevice=IdleDevice,
            ecodes=SimpleNamespace(EV_KEY=1, KEY_RIGHTALT=RIGHT_ALT_KEY),
        )
        controller = RightAltPttController(
            "agent", "session", "project", lambda command: True
        )
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "ptt.lock"
            with lock_path.open("a") as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with (
                    patch.dict(sys.modules, {"evdev": fake_evdev}),
                    patch(
                        "deskhelm_bridge.evdev_ptt.deskhelm_runtime_directory",
                        return_value=Path(temporary),
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, "already owned"):
                        run_right_alt_listener(Path("/dev/input/test"), controller)

        self.assertTrue(created[0].closed)

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
