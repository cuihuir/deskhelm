import os
from pathlib import Path
from unittest import mock
import unittest

from deskhelm_bridge.paths import default_socket_path


class DefaultSocketPathTests(unittest.TestCase):
    def test_uses_deskhelm_directory_in_xdg_runtime_dir(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}):
            self.assertEqual(
                default_socket_path(),
                Path("/run/user/1000/deskhelm/bridge.sock"),
            )

    def test_uses_deskhelm_prefix_for_tmp_fallback(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "deskhelm_bridge.paths.os.getuid", return_value=42
        ):
            self.assertEqual(default_socket_path(), Path("/tmp/deskhelm-42/bridge.sock"))


if __name__ == "__main__":
    unittest.main()
