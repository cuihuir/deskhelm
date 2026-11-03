import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import termios
import unittest
from unittest.mock import Mock, call, patch

from deskhelm_bridge.adapter import AdapterCapability, AdapterSessionAction
from deskhelm_bridge.alpha import (
    ASR_MODEL_DIRECTORY_ENV,
    TTS_DIRECTORY_ENV,
    VOICE_SINK_ENV,
    VOICE_SOURCE_ENV,
    LocalAlphaArtifacts,
    LocalAlphaProfile,
    LocalAlphaRuntime,
    _TerminalState,
    _app_server_command,
    _audio_check_command,
    _bridge_command,
    _child_environment,
    _normalized_return_code,
    _ptt_command,
    _remote_tui_command,
    _restore_terminal,
    _session_event,
    _set_terminal_foreground,
    _stop_tui_process,
    _wait_for_owned_socket,
    run_local_alpha,
)
from deskhelm_bridge.cli import build_parser, normalize_command_argv
from deskhelm_bridge.paths import (
    alpha_codex_socket_paths,
    alpha_directory_digest,
    default_alpha_socket_path,
    default_socket_path,
)


class LocalAlphaProfileTests(unittest.TestCase):
    def test_artifacts_are_injected_by_environment_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = self._artifacts(root)

            loaded = LocalAlphaArtifacts.from_environment(
                {
                    ASR_MODEL_DIRECTORY_ENV: str(artifacts.asr_model_directory),
                    TTS_DIRECTORY_ENV: str(artifacts.tts_directory),
                }
            )
            loaded.validate()

            self.assertEqual(loaded, artifacts)
            self.assertEqual(
                loaded.tts_model_path.name,
                "zh_CN-chaowen-medium.onnx",
            )

    def test_artifact_environment_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, ASR_MODEL_DIRECTORY_ENV):
            LocalAlphaArtifacts.from_environment({})

    def test_kokoro_directory_selects_female_voice_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = self._kokoro_artifacts(root)
            artifacts.validate()
            args = build_parser().parse_args(
                [
                    "codex",
                    "--voice",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                ]
            )
            profile = LocalAlphaProfile.from_args(
                args,
                {
                    ASR_MODEL_DIRECTORY_ENV: str(
                        artifacts.asr_model_directory
                    ),
                    TTS_DIRECTORY_ENV: str(artifacts.tts_directory),
                },
            )
            runtime = LocalAlphaRuntime(
                uv_executable="/usr/bin/uv",
                codex_executable="/usr/bin/codex",
                codex_version="codex-cli 1.0",
                environment={},
            )
            bridge = _bridge_command(profile, runtime)

            self.assertEqual(artifacts.tts_provider, "kokoro")
            self.assertEqual(artifacts.tts_model_path.name, "kokoro-v1_0.pth")
            self.assertEqual(
                bridge[bridge.index("--voice-tts-provider") + 1],
                "kokoro",
            )

    def test_indextts_directory_precedes_piper_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = self._indextts_artifacts(root)
            artifacts.validate()
            self.assertEqual(artifacts.tts_provider, "indextts")
            self.assertEqual(artifacts.tts_model_path, artifacts.tts_directory)
            self.assertEqual(artifacts.tts_voice_path.name, "voice.wav")

            fallback = root / "fallback"
            fallback.mkdir()
            (fallback / "zh_CN-chaowen-medium.onnx").write_bytes(b"test")
            self.assertEqual(
                LocalAlphaArtifacts.from_environment(
                    {
                        ASR_MODEL_DIRECTORY_ENV: str(artifacts.asr_model_directory),
                        TTS_DIRECTORY_ENV: str(fallback),
                    }
                ).tts_provider,
                "piper",
            )

    def test_profile_builds_app_server_bridge_and_ptt_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = self._artifacts(root)
            workdir = root / "project-a"
            workdir.mkdir()
            args = build_parser().parse_args(
                [
                    "codex",
                    "--agent-workdir",
                    str(workdir),
                    "--socket",
                    str(root / "alpha.sock"),
                    "--voice-turn-timing",
                    "--voice",
                    "--tui",
                ]
            )
            profile = LocalAlphaProfile.from_args(
                args,
                {
                    ASR_MODEL_DIRECTORY_ENV: str(artifacts.asr_model_directory),
                    TTS_DIRECTORY_ENV: str(artifacts.tts_directory),
                },
            )
            runtime = LocalAlphaRuntime(
                uv_executable="/usr/bin/uv",
                codex_executable="/usr/bin/codex",
                codex_version="codex-cli 1.0",
                environment={},
            )

            bridge = _bridge_command(profile, runtime)
            app_server = _app_server_command(profile, runtime)
            remote_tui = _remote_tui_command(profile, runtime)
            ptt = _ptt_command(profile)
            registration = _session_event(
                profile,
                runtime.codex_version,
                AdapterSessionAction.REGISTER,
            )

            self.assertEqual(profile.project_id, "project-a")
            self.assertTrue(profile.launch_tui)
            self.assertTrue(profile.voice_enabled)
            self.assertIn("--agent-provider", bridge)
            self.assertIn("codex-app-server", bridge)
            self.assertEqual(
                bridge[bridge.index("--codex-app-server-socket") + 1],
                str(profile.codex_app_server_upstream_socket),
            )
            self.assertEqual(
                bridge[bridge.index("--codex-tui-proxy-socket") + 1],
                str(profile.codex_app_server_socket),
            )
            self.assertEqual(
                app_server,
                [
                    runtime.codex_executable,
                    "app-server",
                    "--listen",
                    f"unix://{profile.codex_app_server_upstream_socket}",
                ],
            )
            self.assertIn(
                f"unix://{profile.codex_app_server_socket}",
                remote_tui,
            )
            self.assertEqual(
                bridge[bridge.index("--voice-vad-provider") + 1],
                "none",
            )
            self.assertIn("--voice-turn-timing", bridge)
            self.assertIn(str(artifacts.asr_model_directory), bridge)
            self.assertIn(str(artifacts.tts_model_path), bridge)
            self.assertEqual(ptt[ptt.index("--project-id") + 1], "project-a")
            self.assertIn("--focused", ptt)
            self.assertEqual(registration.preferred_slot, 0)
            self.assertEqual(
                registration.capabilities,
                (
                    AdapterCapability.INTERACTION_EVENTS,
                    AdapterCapability.APPROVAL_REQUESTS,
                    AdapterCapability.SESSION_RESUME,
                    AdapterCapability.SUBMIT_PROMPT,
                    AdapterCapability.INTERRUPT,
                    AdapterCapability.APPROVAL_DECISIONS,
                ),
            )

            child_environment = _child_environment(
                profile,
                {"VIRTUAL_ENV": "/root/.venv", "PYTHONPATH": "existing"},
            )
            self.assertNotIn("VIRTUAL_ENV", child_environment)
            self.assertTrue(child_environment["PYTHONPATH"].endswith("existing"))

    def test_profile_uses_environment_voice_source_and_cli_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = self._artifacts(root)
            environment = {
                ASR_MODEL_DIRECTORY_ENV: str(artifacts.asr_model_directory),
                TTS_DIRECTORY_ENV: str(artifacts.tts_directory),
                VOICE_SOURCE_ENV: "usb-source",
                VOICE_SINK_ENV: "usb-sink",
            }
            args = build_parser().parse_args(
                [
                    "codex",
                    "--voice",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                ]
            )

            profile = LocalAlphaProfile.from_args(args, environment)

            self.assertEqual(profile.source_name, "usb-source")
            self.assertEqual(profile.sink_name, "usb-sink")
            runtime = LocalAlphaRuntime(
                uv_executable="/usr/bin/uv",
                codex_executable="/usr/bin/codex",
                codex_version="codex-cli 1.0",
                environment={},
            )
            bridge = _bridge_command(profile, runtime)
            audio_check = _audio_check_command(profile, runtime.uv_executable)
            self.assertEqual(
                bridge[bridge.index("--voice-source") + 1],
                "usb-source",
            )
            self.assertEqual(
                audio_check[audio_check.index("--source") + 1],
                "usb-source",
            )
            self.assertEqual(
                audio_check[audio_check.index("--sink") + 1],
                "usb-sink",
            )

            args = build_parser().parse_args(
                [
                    "codex",
                    "--voice",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                    "--voice-source",
                    "cli-source",
                ]
            )

            profile = LocalAlphaProfile.from_args(args, environment)

            self.assertEqual(profile.source_name, "cli-source")

    def test_yolo_remote_tui_command_bypasses_sandbox_and_approvals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = build_parser().parse_args(
                [
                    "codex",
                    "--yolo",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                ]
            )
            profile = LocalAlphaProfile.from_args(args, {})
            runtime = LocalAlphaRuntime(
                uv_executable=None,
                codex_executable="codex",
                codex_version="codex-cli test",
                environment={},
            )

            self.assertTrue(profile.yolo_enabled)
            self.assertEqual(
                _remote_tui_command(profile, runtime),
                [
                    "codex",
                    "--remote",
                    f"unix://{profile.codex_app_server_socket}",
                    "--yolo",
                ],
            )

    def test_check_does_not_start_bridge_or_ptt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = build_parser().parse_args(
                [
                    "codex",
                    "--check",
                    "--tui",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                ]
            )
            environment = {
                ASR_MODEL_DIRECTORY_ENV: str(root / "asr"),
                TTS_DIRECTORY_ENV: str(root / "tts"),
            }
            runtime = LocalAlphaRuntime(
                uv_executable="uv",
                codex_executable="codex",
                codex_version="codex-cli 1.0",
                environment={},
            )
            with (
                patch("deskhelm_bridge.alpha._preflight", return_value=runtime),
                patch("deskhelm_bridge.alpha.subprocess.Popen") as popen,
            ):
                result = run_local_alpha(args, environment=environment)

            self.assertEqual(result, 0)
            popen.assert_not_called()

    def test_core_profile_requires_no_voice_artifacts_or_hardware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = build_parser().parse_args(
                [
                    "codex",
                    "--tui",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                ]
            )

            profile = LocalAlphaProfile.from_args(args, {})
            runtime = LocalAlphaRuntime(
                uv_executable=None,
                codex_executable="/usr/bin/codex",
                codex_version="codex-cli 1.0",
                environment={},
            )
            bridge = _bridge_command(profile, runtime)

            self.assertFalse(profile.voice_enabled)
            self.assertIsNone(profile.artifacts)
            self.assertEqual(bridge[0], sys.executable)
            self.assertNotIn("--voice-provider", bridge)
            child_paths = _child_environment(profile, {})["PYTHONPATH"].split(
                os.pathsep
            )
            self.assertNotIn(
                str(profile.repository_root / "voice"),
                child_paths,
            )

    def test_voice_options_require_explicit_voice_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = build_parser().parse_args(
                [
                    "codex",
                    "--agent-workdir",
                    str(root),
                    "--voice-source",
                    "usb-source",
                ]
            )

            with self.assertRaisesRegex(ValueError, "requires --voice"):
                LocalAlphaProfile.from_args(args, {})

    def test_tui_mode_requires_an_interactive_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = build_parser().parse_args(
                [
                    "codex",
                    "--tui",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                ]
            )
            environment = {
                ASR_MODEL_DIRECTORY_ENV: str(root / "asr"),
                TTS_DIRECTORY_ENV: str(root / "tts"),
            }
            with (
                patch(
                    "deskhelm_bridge.alpha._has_interactive_terminal",
                    return_value=False,
                ),
                patch("deskhelm_bridge.alpha._preflight") as preflight,
            ):
                with self.assertRaisesRegex(ValueError, "interactive terminal"):
                    run_local_alpha(args, environment=environment)

            preflight.assert_not_called()

    def test_tui_mode_launches_remote_in_foreground_and_cleans_stack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = build_parser().parse_args(
                [
                    "codex",
                    "--tui",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                ]
            )
            environment = {
                ASR_MODEL_DIRECTORY_ENV: str(root / "asr"),
                TTS_DIRECTORY_ENV: str(root / "tts"),
            }
            runtime = LocalAlphaRuntime(
                uv_executable="uv",
                codex_executable="codex",
                codex_version="codex-cli 1.0",
                environment={},
            )
            app_server = Mock(pid=100)
            app_server.poll.return_value = None
            bridge = Mock(pid=101)
            bridge.poll.return_value = None
            tui = Mock(pid=102)
            tui.poll.return_value = 0
            publisher = Mock()
            previous_sigterm = object()
            terminal_state = Mock(file_descriptor=0)
            with (
                patch(
                    "deskhelm_bridge.alpha._has_interactive_terminal",
                    return_value=True,
                ),
                patch("deskhelm_bridge.alpha._preflight", return_value=runtime),
                patch(
                    "deskhelm_bridge.alpha.subprocess.Popen",
                    side_effect=(app_server, bridge, tui),
                ) as popen,
                patch(
                    "deskhelm_bridge.alpha._register_session",
                    return_value=publisher,
                ),
                patch("deskhelm_bridge.alpha._wait_for_owned_socket") as ready,
                patch(
                    "deskhelm_bridge.alpha._capture_terminal_state",
                    return_value=terminal_state,
                ),
                patch("deskhelm_bridge.alpha._set_terminal_foreground") as foreground,
                patch("deskhelm_bridge.alpha._restore_terminal") as restore,
                patch(
                    "deskhelm_bridge.alpha.signal.getsignal",
                    return_value=previous_sigterm,
                ),
                patch("deskhelm_bridge.alpha.signal.signal") as set_signal,
                patch("deskhelm_bridge.alpha._stop_process") as stop,
                patch("deskhelm_bridge.alpha._stop_tui_process") as stop_tui,
                patch("deskhelm_bridge.alpha._release_session") as release,
            ):
                result = run_local_alpha(args, environment=environment)

            self.assertEqual(result, 0)
            self.assertEqual(popen.call_count, 3)
            tui_call = popen.call_args_list[2]
            self.assertEqual(tui_call.args[0][0:2], ["codex", "--remote"])
            self.assertNotIn("start_new_session", tui_call.kwargs)
            self.assertNotIn("stdin", tui_call.kwargs)
            self.assertEqual(tui_call.kwargs["process_group"], 0)
            foreground.assert_called_once_with(0, tui.pid)
            self.assertEqual(
                ready.call_args_list,
                [
                    call(
                        root / "alpha-codex-upstream.sock",
                        app_server,
                        "Codex app-server",
                    ),
                    call(
                        root / "alpha-codex.sock",
                        bridge,
                        "Bridge TUI proxy",
                    ),
                ],
            )
            stop_tui.assert_called_once_with(tui)
            restore.assert_called_once_with(terminal_state)
            self.assertNotIn(
                (signal.SIGINT, signal.SIG_IGN),
                (call.args for call in set_signal.call_args_list),
            )
            self.assertEqual(stop.call_count, 2)
            release.assert_called_once()
            publisher.close.assert_called_once_with()

    def test_repeated_interrupt_during_cleanup_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = build_parser().parse_args(
                [
                    "codex",
                    "--tui",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                ]
            )
            runtime = LocalAlphaRuntime(
                uv_executable=None,
                codex_executable="codex",
                codex_version="codex-cli 1.0",
                environment={},
            )
            app_server = Mock(pid=100)
            app_server.poll.return_value = None
            bridge = Mock(pid=101)
            bridge.poll.return_value = None
            tui = Mock(pid=102)
            tui.poll.return_value = 0
            publisher = Mock()
            terminal_state = Mock(file_descriptor=0)
            handlers = {}

            def set_signal(signal_number, handler):
                if callable(handler):
                    handlers[signal_number] = handler

            def interrupt_again(_process):
                handlers[signal.SIGINT](signal.SIGINT, None)

            with (
                patch(
                    "deskhelm_bridge.alpha._has_interactive_terminal",
                    return_value=True,
                ),
                patch("deskhelm_bridge.alpha._preflight", return_value=runtime),
                patch(
                    "deskhelm_bridge.alpha.subprocess.Popen",
                    side_effect=(app_server, bridge, tui),
                ),
                patch(
                    "deskhelm_bridge.alpha._register_session",
                    return_value=publisher,
                ),
                patch("deskhelm_bridge.alpha._focus_session"),
                patch("deskhelm_bridge.alpha._wait_for_owned_socket"),
                patch(
                    "deskhelm_bridge.alpha._capture_terminal_state",
                    return_value=terminal_state,
                ),
                patch("deskhelm_bridge.alpha._set_terminal_foreground"),
                patch("deskhelm_bridge.alpha._restore_terminal"),
                patch("deskhelm_bridge.alpha.signal.getsignal", return_value=None),
                patch(
                    "deskhelm_bridge.alpha.signal.signal",
                    side_effect=set_signal,
                ),
                patch(
                    "deskhelm_bridge.alpha._stop_tui_process",
                    side_effect=interrupt_again,
                ),
                patch("deskhelm_bridge.alpha._stop_process") as stop,
                patch("deskhelm_bridge.alpha._release_session") as release,
            ):
                result = run_local_alpha(args, environment={})

            self.assertEqual(result, 0)
            self.assertEqual(stop.call_count, 2)
            release.assert_called_once()
            publisher.close.assert_called_once_with()

    def test_cleanup_failure_does_not_skip_remaining_children(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = build_parser().parse_args(
                [
                    "codex",
                    "--tui",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                ]
            )
            runtime = LocalAlphaRuntime(
                uv_executable=None,
                codex_executable="codex",
                codex_version="codex-cli 1.0",
                environment={},
            )
            app_server = Mock(pid=100)
            app_server.poll.return_value = None
            bridge = Mock(pid=101)
            bridge.poll.return_value = None
            tui = Mock(pid=102)
            tui.poll.return_value = 0
            publisher = Mock()
            terminal_state = Mock(file_descriptor=0)
            with (
                patch(
                    "deskhelm_bridge.alpha._has_interactive_terminal",
                    return_value=True,
                ),
                patch("deskhelm_bridge.alpha._preflight", return_value=runtime),
                patch(
                    "deskhelm_bridge.alpha.subprocess.Popen",
                    side_effect=(app_server, bridge, tui),
                ),
                patch(
                    "deskhelm_bridge.alpha._register_session",
                    return_value=publisher,
                ),
                patch("deskhelm_bridge.alpha._wait_for_owned_socket"),
                patch(
                    "deskhelm_bridge.alpha._capture_terminal_state",
                    return_value=terminal_state,
                ),
                patch("deskhelm_bridge.alpha._set_terminal_foreground"),
                patch("deskhelm_bridge.alpha._restore_terminal"),
                patch("deskhelm_bridge.alpha.signal.getsignal", return_value=None),
                patch("deskhelm_bridge.alpha.signal.signal"),
                patch(
                    "deskhelm_bridge.alpha._stop_tui_process",
                    side_effect=RuntimeError("synthetic cleanup failure"),
                ),
                patch("deskhelm_bridge.alpha._stop_process") as stop,
                patch("deskhelm_bridge.alpha._release_session") as release,
            ):
                with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                    run_local_alpha(args, environment={})

            self.assertEqual(stop.call_count, 2)
            release.assert_called_once()
            publisher.close.assert_called_once_with()

    def test_tui_signal_exit_is_reported_as_shell_status(self) -> None:
        self.assertEqual(_normalized_return_code(-2), 130)
        self.assertEqual(_normalized_return_code(3), 3)

    def test_tui_stop_escalates_after_bounded_interrupt(self) -> None:
        tui = Mock(pid=103)
        tui.poll.return_value = None
        tui.wait.side_effect = (
            subprocess.TimeoutExpired("codex", 5),
            0,
        )

        with patch("deskhelm_bridge.alpha.os.killpg") as kill_process_group:
            _stop_tui_process(tui)

        self.assertEqual(
            kill_process_group.call_args_list,
            [
                call(tui.pid, signal.SIGINT),
                call(tui.pid, signal.SIGKILL),
            ],
        )
        self.assertEqual(tui.wait.call_count, 2)

    def test_app_server_socket_wait_accepts_bound_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "app-server.sock"
            app_server = Mock()
            app_server.poll.return_value = None
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))

                _wait_for_owned_socket(
                    socket_path,
                    app_server,
                    "Codex app-server",
                )

            app_server.poll.assert_called_once_with()

    def test_tui_terminal_foreground_is_transferred_and_restored(self) -> None:
        previous_sigttou = object()
        state = _TerminalState(
            file_descriptor=7,
            foreground_process_group=41,
            attributes=["terminal-state"],
        )
        with (
            patch(
                "deskhelm_bridge.alpha.signal.getsignal",
                return_value=previous_sigttou,
            ),
            patch("deskhelm_bridge.alpha.signal.signal") as set_signal,
            patch("deskhelm_bridge.alpha.os.tcsetpgrp") as set_foreground,
            patch("deskhelm_bridge.alpha.termios.tcsetattr") as set_attributes,
        ):
            _set_terminal_foreground(7, 103)
            _restore_terminal(state)

        self.assertEqual(
            set_foreground.call_args_list,
            [call(7, 103), call(7, 41)],
        )
        set_attributes.assert_called_once_with(
            7,
            termios.TCSADRAIN,
            ["terminal-state"],
        )
        self.assertEqual(
            set_signal.call_args_list,
            [
                call(signal.SIGTTOU, signal.SIG_IGN),
                call(signal.SIGTTOU, previous_sigttou),
                call(signal.SIGTTOU, signal.SIG_IGN),
                call(signal.SIGTTOU, previous_sigttou),
            ],
        )

    def test_interrupt_stops_all_children_and_releases_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = build_parser().parse_args(
                [
                    "codex",
                    "--voice",
                    "--agent-workdir",
                    str(root),
                    "--socket",
                    str(root / "alpha.sock"),
                ]
            )
            environment = {
                ASR_MODEL_DIRECTORY_ENV: str(root / "asr"),
                TTS_DIRECTORY_ENV: str(root / "tts"),
            }
            runtime = LocalAlphaRuntime(
                uv_executable="uv",
                codex_executable="codex",
                codex_version="codex-cli 1.0",
                environment={},
            )
            app_server = Mock(pid=100)
            app_server.poll.return_value = None
            bridge = Mock(pid=101)
            bridge.poll.return_value = None
            ptt = Mock(pid=102)
            ptt.poll.return_value = None
            publisher = Mock()
            with (
                patch("deskhelm_bridge.alpha._preflight", return_value=runtime),
                patch(
                    "deskhelm_bridge.alpha.subprocess.Popen",
                    side_effect=(app_server, bridge, ptt),
                ),
                patch(
                    "deskhelm_bridge.alpha._register_session",
                    return_value=publisher,
                ),
                patch("deskhelm_bridge.alpha._focus_session"),
                patch("deskhelm_bridge.alpha._wait_for_owned_socket"),
                patch("deskhelm_bridge.alpha.time.sleep", side_effect=KeyboardInterrupt),
                patch("deskhelm_bridge.alpha._stop_process") as stop,
                patch("deskhelm_bridge.alpha._release_session") as release,
            ):
                result = run_local_alpha(args, environment=environment)

            self.assertEqual(result, 130)
            self.assertEqual(stop.call_count, 3)
            release.assert_called_once()
            publisher.close.assert_called_once_with()

    def test_omitted_socket_joins_the_user_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workdir = root / "project-a"
            workdir.mkdir()
            args = build_parser().parse_args(
                ["codex", "--agent-workdir", str(workdir), "--voice-turn-timing", "--voice"]
            )
            artifacts = self._artifacts(root)
            profile = LocalAlphaProfile.from_args(
                args,
                {
                    ASR_MODEL_DIRECTORY_ENV: str(artifacts.asr_model_directory),
                    TTS_DIRECTORY_ENV: str(artifacts.tts_directory),
                },
            )
            runtime = LocalAlphaRuntime(
                uv_executable="/usr/bin/uv",
                codex_executable="/usr/bin/codex",
                codex_version="codex-cli 1.0",
                environment={},
            )
            bridge = _bridge_command(profile, runtime)
            registration = _session_event(
                profile,
                runtime.codex_version,
                AdapterSessionAction.REGISTER,
            )
            proxy, upstream = alpha_codex_socket_paths(
                default_alpha_socket_path(workdir)
            )

            self.assertTrue(profile.shared_bridge)
            self.assertEqual(profile.socket_path, default_socket_path())
            self.assertEqual(
                profile.session_id,
                f"local-{alpha_directory_digest(workdir)}",
            )
            self.assertTrue(profile.client_id.endswith(alpha_directory_digest(workdir)))
            self.assertNotEqual(profile.session_id, "local-alpha")
            self.assertEqual(profile.codex_app_server_socket, proxy)
            self.assertEqual(profile.codex_app_server_upstream_socket, upstream)
            self.assertNotIn("bridge-codex.sock", " ".join(bridge))
            self.assertIn("--agent-runtime-directory", bridge)
            self.assertNotIn("--agent-provider", bridge)
            self.assertEqual(bridge[bridge.index("--slots") + 1], "32")
            self.assertEqual(bridge[bridge.index("--max-connections") + 1], "64")
            self.assertIn("--voice-provider", bridge)
            self.assertIn("--voice-turn-timing", bridge)
            self.assertIsNone(registration.preferred_slot)

    def test_codex_is_the_public_command_and_alpha_still_parses(self) -> None:
        parser = build_parser()
        help_text = parser.format_help()
        self.assertIn("codex", help_text)
        self.assertNotIn("alpha", help_text)
        current = parser.parse_args(["codex", "--tui", "--check"])
        self.assertEqual(current.command, "codex")
        self.assertTrue(current.tui)
        self.assertTrue(current.check)
        legacy = parser.parse_args(normalize_command_argv(["alpha", "--check"]))
        self.assertEqual(legacy.command, "codex")
        self.assertFalse(legacy.tui)
        self.assertTrue(legacy.check)

    @staticmethod
    def _artifacts(root: Path) -> LocalAlphaArtifacts:
        asr = root / "paraformer"
        asr.mkdir()
        for name in ("model.pt", "config.yaml", "tokens.json", "am.mvn", "seg_dict"):
            (asr / name).write_bytes(b"test")
        tts = root / "piper"
        (tts / "g2pW").mkdir(parents=True)
        for name in (
            "zh_CN-chaowen-medium.onnx",
            "zh_CN-chaowen-medium.onnx.json",
            "g2pW/g2pw.onnx",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.txt",
        ):
            (tts / name).write_bytes(b"test")
        return LocalAlphaArtifacts(asr, tts)

    @staticmethod
    def _kokoro_artifacts(root: Path) -> LocalAlphaArtifacts:
        asr = root / "paraformer"
        asr.mkdir()
        for name in ("model.pt", "config.yaml", "tokens.json", "am.mvn", "seg_dict"):
            (asr / name).write_bytes(b"test")
        tts = root / "kokoro"
        tts.mkdir()
        for name in (
            "kokoro-v1_0.pth",
            "config.json",
            "zf_xiaobei.pt",
            "af_heart.pt",
        ):
            (tts / name).write_bytes(b"test")
        return LocalAlphaArtifacts(asr, tts, "kokoro")

    @staticmethod
    def _indextts_artifacts(root: Path) -> LocalAlphaArtifacts:
        asr = root / "paraformer"
        asr.mkdir()
        for name in ("model.pt", "config.yaml", "tokens.json", "am.mvn", "seg_dict"):
            (asr / name).write_bytes(b"test")
        tts = root / "indextts"
        tts.mkdir()
        (tts / "config.yaml").write_bytes(b"test")
        (tts / "voice.wav").write_bytes(b"test")
        return LocalAlphaArtifacts(asr, tts, "indextts")


if __name__ == "__main__":
    unittest.main()
