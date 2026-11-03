from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from deskhelm_cli import USAGE, _options_first, main as deskhelm_main  # noqa: E402
from deskhelm_codex_adapter.host import redaction_arguments  # noqa: E402
from deskhelm_core.server import BridgeServer  # noqa: E402
from deskhelm_host import BridgeLink  # noqa: E402
from deskhelm_host.__main__ import main as host_main  # noqa: E402
from deskhelm_host.bridge_process import bridge_protocol  # noqa: E402
from deskhelm_protocol import SessionKey  # noqa: E402
from deskhelm_protocol.paths import default_socket_path  # noqa: E402
from deskhelm_web.__main__ import DEFAULT_PORT  # noqa: E402
from helpers.v2 import FakeDriver  # noqa: E402


KEY = SessionKey("fake", "session-1", "demo")


def _run(entry, argv: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = entry(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class _V1LikeServer:
    """Answers any first frame the way a v1 Bridge refuses a v2 hello."""

    def __init__(self, path: Path) -> None:
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        self.listener.listen(4)
        self.listener.settimeout(0.2)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except OSError:
                continue
            with connection:
                connection.settimeout(1)
                try:
                    connection.recv(65536)
                    connection.sendall(b'{"message_type":"protocol_error","error_code":"invalid_frame"}\n')
                except OSError:
                    pass

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)
        self.listener.close()


class SwitchOverTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "bridge.sock"

    def _bridge_with_session(self) -> None:
        server = BridgeServer(self.path, tick_seconds=0.05)
        server.start()
        self.addCleanup(server.close)
        driver = FakeDriver()
        link = BridgeLink(self.path, KEY, driver, client_id="host-test", reconnect_initial=0.05, reconnect_max=0.1)
        link.start()
        self.addCleanup(link.close)
        self.assertTrue(link.opened.wait(3))

    def test_the_user_bridge_and_page_take_the_v1_names(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.directory)}):
            self.assertEqual(default_socket_path(), self.directory / "deskhelm" / "bridge.sock")
        self.assertEqual(DEFAULT_PORT, 8766)

    def test_commands_run_the_v2_hosts_and_surfaces(self) -> None:
        self._bridge_with_session()
        listed = _run(deskhelm_main, ["sessions", "--socket", str(self.path)])
        focused = _run(deskhelm_main, ["focus", "--socket", str(self.path), "--agent-id", "fake",
                                       "--session-id", "session-1", "--project-id", "demo"])

        self.assertEqual(listed[0], 0)
        self.assertIn("fake/session-1/demo", listed[1])
        self.assertEqual((focused[0], focused[1].strip()), (0, "已聚焦这个会话"))
        self.assertEqual(_run(deskhelm_main, ["--help"])[:2], (0, USAGE))
        self.assertEqual(_run(deskhelm_main, [])[0], 2)
        unknown = _run(deskhelm_main, ["ptt"])
        self.assertEqual(unknown[0], 2)
        self.assertIn("unknown command 'ptt'", unknown[2])
        self.assertEqual(_options_first("focus", ["--agent-id", "a", "--socket", "/s"]),
                         ["--socket", "/s", "focus", "--agent-id", "a"])

    def test_a_socket_that_is_not_a_v2_bridge_is_named_instead_of_retried(self) -> None:
        old = _V1LikeServer(self.path)
        self.addCleanup(old.close)
        missing = bridge_protocol(self.directory / "absent.sock")
        foreign = bridge_protocol(self.path)
        code, _, stderr = _run(host_main, ["claude", "--socket", str(self.path)])

        self.assertEqual((missing, foreign), ("none", "other"))
        self.assertEqual(code, 1)
        self.assertIn("other than a v2 Bridge", stderr)

    def test_a_stopped_agent_host_reports_the_signal_not_a_failure(self) -> None:
        self._bridge_with_session()
        bin_directory = self.directory / "bin"
        bin_directory.mkdir()
        claude = bin_directory / "claude"
        claude.write_text('#!/usr/bin/env bash\n[ "$1" = --version ] && { echo "2.1.312 (Claude Code)"; exit 0; }\n'
                          "exec cat >/dev/null\n")
        claude.chmod(0o755)
        environment = {**os.environ, "PATH": f"{bin_directory}{os.pathsep}{os.environ['PATH']}"}
        host = subprocess.Popen([sys.executable, "-m", "deskhelm_host", "claude", "--socket", str(self.path)],
                                cwd=self.directory, env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: host.poll() is None and host.kill())
        assert host.stderr is not None
        self.addCleanup(host.stderr.close)
        started = ""
        deadline = time.monotonic() + 10
        while "claude session" not in started and time.monotonic() < deadline:
            if select.select([host.stderr], [], [], 0.2)[0]:
                started += host.stderr.readline()

        host.send_signal(signal.SIGTERM)

        self.assertIn("claude session", started)
        self.assertEqual(host.wait(timeout=10), 128 + signal.SIGTERM)

    def test_a_running_v2_bridge_is_recognized(self) -> None:
        self._bridge_with_session()

        self.assertEqual(bridge_protocol(self.path), "v2")

    def test_codex_options_stay_with_codex(self) -> None:
        for argv in (["claude", "--yolo"], ["grok", "--voice"], ["codex", "--default-source"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                host_main([*argv, "--socket", str(self.path)])

    def test_the_codex_preflight_finds_codex(self) -> None:
        codex = self.directory / "codex"
        codex.write_text("#!/usr/bin/env bash\necho 'codex-cli 9.9.9'\n")
        codex.chmod(0o755)
        code, _, stderr = _run(host_main, ["codex", "--check", "--codex-executable", str(codex), "--socket", str(self.path)])

        self.assertEqual(code, 0)
        self.assertIn("codex-cli 9.9.9 found", stderr)
        self.assertIn("codex preflight passed", stderr)

    def test_injected_voice_settings_are_redacted_from_the_tui(self) -> None:
        environment = {"DESKHELM_ALPHA_VOICE_SOURCE": "usb-mic", "DESKHELM_ALPHA_TTS_DIRECTORY": "/models/tts",
                       "DESKHELM_ALPHA_VOICE_SINK": "", "DESKHELM_ALPHA_ASR_MODEL_DIRECTORY": ""}
        with mock.patch.dict(os.environ, environment):
            arguments = redaction_arguments()

        self.assertEqual(arguments, ["--redact-env", "DESKHELM_ALPHA_VOICE_SOURCE", "--redact-env", "DESKHELM_ALPHA_TTS_DIRECTORY"])


if __name__ == "__main__":
    unittest.main()
