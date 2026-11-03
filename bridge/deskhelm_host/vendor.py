"""Own one vendor CLI process: prompts go to stdin, never into argv."""

from __future__ import annotations

from collections.abc import Iterator
import json
import os
from pathlib import Path
import select
import signal
import subprocess
from threading import Lock
from typing import Any

MAX_LINE_BYTES = 1024 * 1024


class VendorClosed(Exception):
    """The vendor process is gone, so a write never reached it."""


class VendorProcess:
    def __init__(self, argv: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> None:
        if not argv:
            raise ValueError("vendor argv must not be empty")
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self._process: subprocess.Popen[bytes] | None = None
        self._write_lock = Lock()
        self._closed = False

    def start(self) -> None:
        self._process = subprocess.Popen(
            self.argv,
            cwd=self.cwd,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None and not self._closed

    def write_line(self, value: dict[str, Any]) -> None:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(data) > MAX_LINE_BYTES:
            raise ValueError("vendor line is too long")
        with self._write_lock:
            process = self._process
            if process is None or process.stdin is None or not self.alive():
                raise VendorClosed()
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except (BrokenPipeError, ValueError) as error:
                raise VendorClosed() from error

    def read_batches(self) -> Iterator[list[dict[str, Any]]]:
        """Yield the JSON objects from each read, so a driver can coalesce deltas.

        An overlong line stops the vendor instead of dropping output.
        """
        process = self._process
        if process is None or process.stdout is None:
            return
        descriptor = process.stdout.fileno()
        buffer = bytearray()
        while True:
            readable, _, _ = select.select([descriptor], [], [], 0.5)
            if not readable:
                if process.poll() is not None:
                    return
                continue
            chunk = os.read(descriptor, 65536)
            if not chunk:
                return
            buffer.extend(chunk)
            batch: list[dict[str, Any]] = []
            while True:
                index = buffer.find(b"\n")
                if index < 0:
                    break
                line = bytes(buffer[:index])
                del buffer[: index + 1]
                if len(line) > MAX_LINE_BYTES:
                    self.terminate()
                    return
                try:
                    value = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    batch.append(value)
            if len(buffer) > MAX_LINE_BYTES:
                self.terminate()
                return
            if batch:
                yield batch

    def wait(self) -> int:
        if self._process is None:
            return 0
        return self._process.wait()

    def terminate(self, grace: float = 2.0) -> None:
        process = self._process
        self._closed = True
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=grace)
        except ProcessLookupError:
            pass
