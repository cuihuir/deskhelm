from __future__ import annotations

import contextlib
import fcntl
import io
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

from deskhelm_voice_host import keys


class _Device:
    events: list[SimpleNamespace] = []

    def __init__(self, path: str) -> None:
        if path == "/missing":
            raise OSError(2, "No such device")

    def read_loop(self):
        yield from self.events

    def close(self) -> None:
        return None


def _evdev() -> ModuleType:
    module = ModuleType("evdev")
    module.InputDevice = _Device
    module.ecodes = SimpleNamespace(EV_KEY=1, KEY_RIGHTALT=100)
    return module


class RightAltKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runtime = Path(directory.name)
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(sys.modules, {"evdev": _evdev()}), contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = keys.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_right_alt_edges_become_lines_and_other_keys_are_ignored(self) -> None:
        _Device.events = [SimpleNamespace(type=1, code=100, value=1), SimpleNamespace(type=1, code=100, value=2),
                          SimpleNamespace(type=1, code=30, value=1), SimpleNamespace(type=4, code=100, value=0),
                          SimpleNamespace(type=1, code=100, value=0)]

        self.assertEqual(self._run("--device", "/dev/input/test"), (0, "down\nup\n", ""))
        self.assertEqual(self._run("--device", "/dev/input/test", "--check")[:2], (0, "ok\n"))

    def test_one_listener_holds_the_key_and_failures_are_named(self) -> None:
        directory = self.runtime / "deskhelm"
        directory.mkdir(mode=0o700)
        with (directory / "ptt.lock").open("a") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            taken = self._run("--device", "/dev/input/test")
        missing = self._run("--device", "/missing")
        with mock.patch.dict(sys.modules, {"evdev": None}), contextlib.redirect_stderr(io.StringIO()) as stderr:
            without_evdev = keys.main(["--device", "/dev/input/test"])

        self.assertEqual(taken[0], 1)
        self.assertIn("another right-Alt listener", taken[2])
        self.assertEqual(missing[0], 1)
        self.assertIn("cannot read /missing", missing[2])
        self.assertEqual(without_evdev, 2)
        self.assertIn("uv sync --extra ptt", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
