import os
from pathlib import Path
import socket
import tempfile
from unittest import mock
import unittest

from deskhelm_bridge.paths import (
    default_alpha_socket_path,
    default_socket_path,
    resolve_web_socket,
    running_alpha_sockets,
)


class DefaultSocketPathTests(unittest.TestCase):
    def test_alpha_socket_is_stable_and_directory_scoped(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}):
            first = default_alpha_socket_path(Path("/work/one"))
            second = default_alpha_socket_path(Path("/work/two"))
        self.assertEqual(first, default_alpha_socket_path(Path("/work/one")))
        self.assertNotEqual(first, second)
        self.assertTrue(str(first).startswith("/run/user/1000/deskhelm/alpha-one-"))

    def test_uses_deskhelm_directory_in_xdg_runtime_dir(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}):
            self.assertEqual(
                default_socket_path(),
                Path("/run/user/1000/deskhelm/bridge.sock"),
            )

    def test_web_socket_uses_the_user_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            self._listen(runtime / "deskhelm" / "alpha-luna_niao_-84b66d6be8de.sock")
            self._listen(runtime / "deskhelm" / "alpha-other-0123456789ab.sock")
            self._listen(runtime / "deskhelm" / "alpha-luna_niao_-84b66d6be8de-codex.sock")
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
                selected = resolve_web_socket(None, Path("/work/other"))
                waiting = resolve_web_socket(None, Path("/work/missing"))
                listed = running_alpha_sockets()
            self.assertEqual(selected, Path(directory) / "deskhelm" / "bridge.sock")
            self.assertEqual(waiting, selected)
            self.assertEqual(len(listed), 2)
            self.assertFalse(selected.exists())

    def test_web_socket_rejects_an_empty_path_and_keeps_an_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
                with self.assertRaises(ValueError):
                    resolve_web_socket(Path(" "))
                explicit = resolve_web_socket(
                    Path("/tmp/chosen.sock"), Path("/work/missing")
                )
            self.assertEqual(explicit, Path("/tmp/chosen.sock"))

    def _listen(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(1)
        self.addCleanup(server.close)

    def test_uses_deskhelm_prefix_for_tmp_fallback(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "deskhelm_bridge.paths.os.getuid", return_value=42
        ):
            self.assertEqual(default_socket_path(), Path("/tmp/deskhelm-42/bridge.sock"))


if __name__ == "__main__":
    unittest.main()
