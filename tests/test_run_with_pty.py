import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


TOOL = Path(__file__).resolve().parents[1] / "tools" / "run-with-pty.py"
SPEC = importlib.util.spec_from_file_location("deskhelm_run_with_pty", TOOL)
assert SPEC is not None and SPEC.loader is not None
PTY_TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PTY_TOOL)
REDACTED = PTY_TOOL.REDACTED
KITTY_CLEAR_INPUT = PTY_TOOL.KITTY_CLEAR_INPUT
KITTY_CTRL_C = PTY_TOOL.KITTY_CTRL_C
RAW_CLEAR_INPUT = PTY_TOOL.RAW_CLEAR_INPUT
RAW_CTRL_C = PTY_TOOL.RAW_CTRL_C
StreamingRedactor = PTY_TOOL._StreamingRedactor
TuiInputFilter = PTY_TOOL._TuiInputFilter


class RunWithPtyTests(unittest.TestCase):
    tool = TOOL

    def test_child_receives_terminal_file_descriptors(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(self.tool),
                "--",
                sys.executable,
                "-c",
                (
                    "import os; "
                    "print(os.isatty(0), os.isatty(1), os.isatty(2))"
                ),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"True True True", result.stdout.replace(b"\r", b""))

    def test_child_exit_status_is_preserved(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(self.tool),
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(7)",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 7)

    def test_child_exit_is_reaped_when_grandchild_holds_pty_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "grandchild.pid"
            script = (
                "import pathlib, subprocess, sys; "
                "child = subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(60)'], start_new_session=True); "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(child.pid)); "
                "raise SystemExit(0)"
            )
            started = time.monotonic()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(self.tool),
                        "--",
                        sys.executable,
                        "-c",
                        script,
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=3,
                )
            finally:
                if pid_path.exists():
                    grandchild_pid = int(pid_path.read_text())
                    try:
                        os.killpg(grandchild_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(time.monotonic() - started, 2)

    def test_streaming_redaction_handles_split_values_without_newlines(self) -> None:
        redactor = StreamingRedactor((b"sensitive-value",))

        output = b"".join(
            (
                redactor.feed(b"prefix sensitive-"),
                redactor.feed(b"value suffix"),
                redactor.finish(),
            )
        )

        self.assertEqual(output, b"prefix " + REDACTED + b" suffix")

    def test_redaction_values_are_loaded_from_environment(self) -> None:
        environment = {**os.environ, "DESKHELM_TEST_SECRET": "hidden-value"}
        result = subprocess.run(
            [
                sys.executable,
                str(self.tool),
                "--redact-env",
                "DESKHELM_TEST_SECRET",
                "--",
                sys.executable,
                "-c",
                "import os; os.write(1, b'hidden-'); os.write(1, b'value')",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=environment,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, REDACTED)

    def test_raw_ctrl_c_requires_a_second_press(self) -> None:
        input_filter = TuiInputFilter(confirm_seconds=1)

        first = input_filter.feed(RAW_CTRL_C, now=10)
        second = input_filter.feed(RAW_CTRL_C, now=10.5)

        self.assertEqual(first, RAW_CLEAR_INPUT)
        self.assertEqual(second, RAW_CTRL_C)

    def test_ctrl_c_confirmation_expires_and_resets_on_input(self) -> None:
        input_filter = TuiInputFilter(confirm_seconds=1)

        output = b"".join(
            (
                input_filter.feed(RAW_CTRL_C, now=10),
                input_filter.feed(RAW_CTRL_C, now=12),
                input_filter.feed(b"x", now=12.1),
                input_filter.feed(RAW_CTRL_C, now=12.2),
            )
        )

        self.assertEqual(
            output,
            RAW_CLEAR_INPUT + RAW_CLEAR_INPUT + b"x" + RAW_CLEAR_INPUT,
        )

    def test_split_kitty_ctrl_c_sequence_requires_a_second_press(self) -> None:
        input_filter = TuiInputFilter(confirm_seconds=1)

        first_part = input_filter.feed(KITTY_CTRL_C[:4], now=10)
        first_end = input_filter.feed(KITTY_CTRL_C[4:], now=10.01)
        second = input_filter.feed(KITTY_CTRL_C, now=10.5)

        self.assertEqual(first_part, b"")
        self.assertEqual(first_end, KITTY_CLEAR_INPUT)
        self.assertEqual(second, KITTY_CTRL_C)

    def test_incomplete_kitty_sequence_flushes_after_timeout(self) -> None:
        input_filter = TuiInputFilter(sequence_timeout_seconds=0.01)

        pending = input_filter.feed(b"\x1b", now=10)
        flushed = input_filter.flush(now=10.02)

        self.assertEqual(pending, b"")
        self.assertEqual(flushed, b"\x1b")


if __name__ == "__main__":
    unittest.main()
