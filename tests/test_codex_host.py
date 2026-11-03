from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from deskhelm_codex_adapter.host import codex_sockets, run_codex_host, tui_command
from deskhelm_protocol import SessionKey


class CodexHostTests(unittest.TestCase):
    def test_the_tui_asks_for_approval_unless_yolo_is_explicit(self) -> None:
        proxy = Path("/run/user/1000/deskhelm/codex-abc-tui.sock")

        normal = tui_command("codex", proxy, sandbox="read-only", yolo=False)
        yolo = tui_command("codex", proxy, sandbox="read-only", yolo=True)

        self.assertEqual(normal, ["codex", "--remote", f"unix://{proxy}", "--sandbox", "read-only", "--ask-for-approval", "on-request"])
        self.assertEqual(yolo, ["codex", "--remote", f"unix://{proxy}", "--yolo"])

    def test_sockets_are_per_directory_and_apart_from_the_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
            app, tui = codex_sockets("0123456789ab")
            other = codex_sockets("ba9876543210")

        self.assertEqual((app.parent, tui.parent), (Path(directory) / "deskhelm",) * 2)
        self.assertEqual((app.name, tui.name), ("codex-0123456789ab-app.sock", "codex-0123456789ab-tui.sock"))
        self.assertFalse({app, tui} & set(other))
        self.assertNotIn("bridge.sock", (app.name, tui.name))

    def test_a_terminal_is_required(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO()), mock.patch.object(sys, "stderr", stderr):
            code = run_codex_host(
                cwd=Path.cwd(),
                session=SessionKey("codex", "local-1", "demo"),
                digest="0123456789ab",
                socket_path=Path("/tmp/deskhelm-unused.sock"),
                client_id="test",
            )

        self.assertEqual(code, 2)
        self.assertIn("interactive terminal", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
