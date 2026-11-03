from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import os
import signal
import socket
import subprocess
from threading import Lock, Thread
import time


MAX_RELAY_LINE_BYTES = 1024 * 1024
_QUEUE_LIMIT = 256


class StdioRelay:
    """Own one vendor process and expose its stdio as a single local socket.

    The bytes are not parsed. A line over 1 MiB, or a full buffer before a
    client connects, stops the process instead of dropping output.
    """

    def __init__(
        self,
        socket_path: Path,
        command: list[str],
        cwd: Path,
        preamble: tuple[bytes, ...] = (),
    ) -> None:
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("relay command is invalid")
        if any(
            not isinstance(line, bytes) or len(line) > MAX_RELAY_LINE_BYTES or not line.endswith(b"\n")
            for line in preamble
        ):
            raise ValueError("relay preamble is invalid")
        self.socket_path = socket_path
        self.command = list(command)
        self.cwd = cwd
        self._preamble = preamble
        self._closed = False
        self._listener: socket.socket | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._client: socket.socket | None = None
        self._queue: Queue[bytes] = Queue(maxsize=_QUEUE_LIMIT)
        self._lock = Lock()

    def serve(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise RuntimeError("agent relay socket already exists")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.socket_path))
        except OSError as error:
            listener.close()
            raise RuntimeError("agent relay socket already exists") from error
        os.chmod(self.socket_path, 0o600)
        listener.listen(1)
        listener.settimeout(0.2)
        self._listener = listener
        self._process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )
        Thread(target=self._read_stdout, name="deskhelm-relay-stdout", daemon=True).start()
        for line in self._preamble:
            self._write_stdin(line)
        Thread(target=self._accept, name="deskhelm-relay-accept", daemon=True).start()

    def wait(self) -> int:
        process = self._process
        if process is None:
            return 1
        return process.wait()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            listener = self._listener
            client = self._client
            process = self._process
        if listener is not None:
            listener.close()
        if client is not None:
            client.close()
        if process is not None and process.poll() is None:
            _terminate_group(process)
        try:
            if self.socket_path.exists() or self.socket_path.is_symlink():
                self.socket_path.unlink()
        except OSError:
            pass

    def _accept(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while not self._closed:
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                if self._closed:
                    client.close()
                    return
                self._client = client
            Thread(
                target=self._write_socket,
                args=(client,),
                name="deskhelm-relay-write",
                daemon=True,
            ).start()
            self._read_socket(client)
            return

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        pending = bytearray()
        fd = process.stdout.fileno()
        while not self._closed:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                return
            if not chunk:
                break
            pending.extend(chunk)
            if not self._drain_lines(pending):
                return
            if len(pending) > MAX_RELAY_LINE_BYTES:
                self.close()
                return
        if pending and not self._closed:
            self._enqueue(bytes(pending))

    def _drain_lines(self, pending: bytearray) -> bool:
        while True:
            index = pending.find(b"\n")
            if index < 0:
                return True
            line = bytes(pending[:index])
            del pending[: index + 1]
            if len(line) > MAX_RELAY_LINE_BYTES:
                self.close()
                return False
            if not self._enqueue(line + b"\n"):
                return False

    def _enqueue(self, line: bytes) -> bool:
        if self._closed:
            return False
        try:
            self._queue.put(line, timeout=5)
            return True
        except Exception:
            self.close()
            return False

    def _write_socket(self, client: socket.socket) -> None:
        process = self._process
        while not self._closed:
            try:
                line = self._queue.get(timeout=0.2)
            except Empty:
                if process is not None and process.poll() is not None and self._queue.empty():
                    try:
                        client.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    return
                continue
            try:
                client.sendall(line)
            except OSError:
                self.close()
                return

    def _read_socket(self, client: socket.socket) -> None:
        pending = bytearray()
        client.settimeout(0.2)
        while not self._closed:
            try:
                chunk = client.recv(4096)
            except socket.timeout:
                process = self._process
                if process is not None and process.poll() is not None:
                    return
                continue
            except OSError:
                return
            if not chunk:
                self._stop_process()
                return
            pending.extend(chunk)
            while True:
                index = pending.find(b"\n")
                if index < 0:
                    break
                line = bytes(pending[:index])
                del pending[: index + 1]
                if len(line) > MAX_RELAY_LINE_BYTES:
                    self.close()
                    return
                self._write_stdin(line + b"\n")
            if len(pending) > MAX_RELAY_LINE_BYTES:
                self.close()
                return

    def _write_stdin(self, line: bytes) -> None:
        process = self._process
        if process is None or process.stdin is None:
            return
        try:
            process.stdin.write(line)
            process.stdin.flush()
        except OSError:
            self.close()

    def _stop_process(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            _terminate_group(process)


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        process.terminate()
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        process.kill()
