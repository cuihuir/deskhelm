#!/usr/bin/env python3
from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
import tty


BUFFER_SIZE = 4096
MAX_PENDING_BYTES = 64 * 1024
DEFAULT_WINDOW_SIZE = struct.pack("HHHH", 24, 80, 0, 0)
REDACTED = b"[REDACTED]"
RAW_CTRL_C = b"\x03"
RAW_CLEAR_INPUT = b"\x15"
KITTY_CTRL_C = b"\x1b[99;5u"
KITTY_CLEAR_INPUT = b"\x1b[117;5u"
DEFAULT_CTRL_C_CONFIRM_SECONDS = 1.5
KEY_SEQUENCE_TIMEOUT_SECONDS = 0.03
CHILD_EXIT_DRAIN_SECONDS = 0.2
CHILD_STATUS_POLL_SECONDS = 0.05


class _StreamingRedactor:
    def __init__(self, values: tuple[bytes, ...]) -> None:
        self._values = tuple(
            sorted({value for value in values if value}, key=len, reverse=True)
        )
        self._buffer = b""

    def feed(self, data: bytes) -> bytes:
        self._buffer += data
        return self._drain(final=False)

    def finish(self) -> bytes:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> bytes:
        output = bytearray()
        while self._buffer:
            complete = [
                value for value in self._values if self._buffer.startswith(value)
            ]
            if not final and any(
                len(value) > len(self._buffer)
                and value.startswith(self._buffer)
                for value in self._values
            ):
                break
            if complete:
                matched = complete[0]
                output.extend(REDACTED)
                self._buffer = self._buffer[len(matched) :]
                continue
            output.append(self._buffer[0])
            self._buffer = self._buffer[1:]
        return bytes(output)


class _TuiInputFilter:
    def __init__(
        self,
        *,
        confirm_seconds: float = DEFAULT_CTRL_C_CONFIRM_SECONDS,
        sequence_timeout_seconds: float = KEY_SEQUENCE_TIMEOUT_SECONDS,
    ) -> None:
        if confirm_seconds <= 0:
            raise ValueError("Ctrl-C confirmation window must be positive")
        if sequence_timeout_seconds <= 0:
            raise ValueError("key sequence timeout must be positive")
        self._confirm_seconds = confirm_seconds
        self._sequence_timeout_seconds = sequence_timeout_seconds
        self._buffer = b""
        self._sequence_deadline: float | None = None
        self._ctrl_c_armed_until: float | None = None

    def feed(self, data: bytes, *, now: float | None = None) -> bytes:
        self._buffer += data
        return self._drain(_monotonic(now), final=False)

    def flush(self, *, now: float | None = None) -> bytes:
        return self._drain(_monotonic(now), final=False)

    def finish(self, *, now: float | None = None) -> bytes:
        return self._drain(_monotonic(now), final=True)

    def pending_deadline(self) -> float | None:
        return self._sequence_deadline

    def _drain(self, now: float, *, final: bool) -> bytes:
        output = bytearray()
        while self._buffer:
            if self._buffer.startswith(KITTY_CTRL_C):
                self._buffer = self._buffer[len(KITTY_CTRL_C) :]
                self._sequence_deadline = None
                output.extend(
                    self._handle_ctrl_c(
                        original=KITTY_CTRL_C,
                        clear_input=KITTY_CLEAR_INPUT,
                        now=now,
                    )
                )
                continue

            is_partial_ctrl_c = KITTY_CTRL_C.startswith(self._buffer)
            if is_partial_ctrl_c and not final:
                if self._sequence_deadline is None:
                    self._sequence_deadline = (
                        now + self._sequence_timeout_seconds
                    )
                if now < self._sequence_deadline:
                    break

            value = self._buffer[:1]
            self._buffer = self._buffer[1:]
            self._sequence_deadline = None
            if value == RAW_CTRL_C:
                output.extend(
                    self._handle_ctrl_c(
                        original=RAW_CTRL_C,
                        clear_input=RAW_CLEAR_INPUT,
                        now=now,
                    )
                )
            else:
                self._ctrl_c_armed_until = None
                output.extend(value)
        return bytes(output)

    def _handle_ctrl_c(
        self,
        *,
        original: bytes,
        clear_input: bytes,
        now: float,
    ) -> bytes:
        if (
            self._ctrl_c_armed_until is not None
            and now <= self._ctrl_c_armed_until
        ):
            self._ctrl_c_armed_until = None
            return original
        self._ctrl_c_armed_until = now + self._confirm_seconds
        return clear_input


def _monotonic(value: float | None) -> float:
    return time.monotonic() if value is None else value


def _copy_window_size(source_fd: int, master_fd: int) -> None:
    try:
        window_size = fcntl.ioctl(
            source_fd,
            termios.TIOCGWINSZ,
            b"\0" * len(DEFAULT_WINDOW_SIZE),
        )
    except OSError:
        window_size = DEFAULT_WINDOW_SIZE
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, window_size)


def _relay(
    master_fd: int,
    output_fd: int,
    redactor: _StreamingRedactor,
    input_filter: _TuiInputFilter | None,
    child_pid: int,
) -> None:
    input_buffer = b""
    output_buffer = b""
    stdin_open = True
    master_open = True
    child_exit_deadline: float | None = None
    os.set_blocking(master_fd, False)

    while master_open or output_buffer:
        now = time.monotonic()
        if child_exit_deadline is None and _child_exited(child_pid):
            child_exit_deadline = now + CHILD_EXIT_DRAIN_SECONDS
            stdin_open = False
            input_buffer = b""
        if child_exit_deadline is not None and now >= child_exit_deadline:
            master_open = False
            input_buffer = b""
            output_buffer += redactor.finish()

        readable: list[int] = []
        writable: list[int] = []
        if stdin_open and master_open and len(input_buffer) < MAX_PENDING_BYTES:
            readable.append(sys.stdin.fileno())
        if master_open and len(output_buffer) < MAX_PENDING_BYTES:
            readable.append(master_fd)
        if input_buffer and master_open:
            writable.append(master_fd)
        if output_buffer:
            writable.append(output_fd)

        select_timeout = CHILD_STATUS_POLL_SECONDS
        if input_filter is not None:
            pending_deadline = input_filter.pending_deadline()
            if pending_deadline is not None:
                select_timeout = min(
                    select_timeout,
                    max(0.0, pending_deadline - time.monotonic()),
                )
        if child_exit_deadline is not None:
            select_timeout = min(
                select_timeout,
                max(0.0, child_exit_deadline - time.monotonic()),
            )

        try:
            ready_read, ready_write, _ = select.select(
                readable,
                writable,
                [],
                select_timeout,
            )
        except InterruptedError:
            continue

        if input_filter is not None:
            input_buffer += input_filter.flush()

        if output_fd in ready_write:
            written = os.write(
                output_fd,
                output_buffer[:BUFFER_SIZE],
            )
            output_buffer = output_buffer[written:]

        if master_fd in ready_read:
            try:
                data = os.read(master_fd, BUFFER_SIZE)
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                data = b""
            if data:
                output_buffer += redactor.feed(data)
            else:
                master_open = False
                input_buffer = b""
                output_buffer += redactor.finish()

        if master_fd in ready_write:
            try:
                written = os.write(master_fd, input_buffer[:BUFFER_SIZE])
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                master_open = False
                input_buffer = b""
            else:
                input_buffer = input_buffer[written:]

        if sys.stdin.fileno() in ready_read:
            data = os.read(sys.stdin.fileno(), BUFFER_SIZE)
            if data:
                input_buffer += (
                    input_filter.feed(data)
                    if input_filter is not None
                    else data
                )
            else:
                stdin_open = False
                if input_filter is not None:
                    input_buffer += input_filter.finish()


def _child_exited(child_pid: int) -> bool:
    result = os.waitid(
        os.P_PID,
        child_pid,
        os.WEXITED | os.WNOHANG | os.WNOWAIT,
    )
    return result is not None


def run_with_pty(
    command: list[str],
    redaction_values: tuple[bytes, ...],
    *,
    guard_ctrl_c: bool = False,
) -> int:
    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        os.execvp(command[0], command)

    stdin_fd = sys.stdin.fileno()
    previous_window_handler = signal.getsignal(signal.SIGWINCH)
    terminal_attributes: list[object] | None = None
    child_waited = False
    output_fd = sys.stdout.fileno()
    close_output = False

    def resize(_signal_number: int, _frame: object) -> None:
        try:
            _copy_window_size(stdin_fd, master_fd)
        except OSError:
            pass

    try:
        _copy_window_size(stdin_fd, master_fd)
        signal.signal(signal.SIGWINCH, resize)
        if os.isatty(stdin_fd):
            terminal_attributes = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)
            try:
                output_fd = os.open(
                    "/dev/tty",
                    os.O_WRONLY | os.O_NOCTTY,
                )
                close_output = True
            except OSError:
                output_fd = sys.stdout.fileno()
        _relay(
            master_fd,
            output_fd,
            _StreamingRedactor(redaction_values),
            _TuiInputFilter() if guard_ctrl_c else None,
            child_pid,
        )
        _, wait_status = os.waitpid(child_pid, 0)
        child_waited = True
    finally:
        signal.signal(signal.SIGWINCH, previous_window_handler)
        if terminal_attributes is not None:
            termios.tcsetattr(
                stdin_fd,
                termios.TCSAFLUSH,
                terminal_attributes,
            )
        if close_output:
            os.close(output_fd)
        os.close(master_fd)
        if not child_waited:
            try:
                os.kill(child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(child_pid, 0)
            except ChildProcessError:
                pass

    exit_code = os.waitstatus_to_exitcode(wait_status)
    return exit_code if exit_code >= 0 else 128 - exit_code


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    redaction_names: list[str] = []
    guard_ctrl_c = False
    while arguments and arguments[0] != "--":
        if arguments[0] == "--redact-env":
            if len(arguments) < 2 or not arguments[1].strip():
                print(
                    "run-with-pty: --redact-env requires a name",
                    file=sys.stderr,
                )
                return 2
            redaction_names.append(arguments[1])
            arguments = arguments[2:]
            continue
        if arguments[0] == "--guard-ctrl-c":
            guard_ctrl_c = True
            arguments = arguments[1:]
            continue
        break
    if not arguments or arguments[0] != "--":
        print("run-with-pty: '--' is required before the command", file=sys.stderr)
        return 2
    arguments = arguments[1:]
    if not arguments:
        print("run-with-pty: command is required", file=sys.stderr)
        return 2
    missing = [name for name in redaction_names if not os.environ.get(name)]
    if missing:
        print(
            "run-with-pty: missing redaction environment names "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 2
    redaction_values = tuple(
        os.environ[name].encode() for name in redaction_names
    )
    return run_with_pty(
        arguments,
        redaction_values,
        guard_ctrl_c=guard_ctrl_c,
    )


if __name__ == "__main__":
    raise SystemExit(main())
