from __future__ import annotations

import json
import socket
from threading import Lock, Thread
import time

from deskhelm_bridge.control import ControlCommand, ControlKind
from deskhelm_bridge.runtime_directory import RuntimeAttachSpec

from .stream import GrokEventDecoder


class GrokRelayRuntime:
    """Read one Grok ACP relay and publish DeskHelm events."""

    def __init__(self, spec: RuntimeAttachSpec) -> None:
        if spec.relay_socket is None:
            raise ValueError("relay socket is required")
        self.relay_socket = spec.relay_socket
        self._decoder = GrokEventDecoder(spec.session, spec.source_version)
        self._socket: socket.socket | None = None
        self._publish = None
        self._closed = False
        self._write_lock = Lock()
        self._unregisters: list = []

    def start(self, publish, facade) -> None:
        self._publish = publish
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(self.relay_socket))
        except OSError as error:
            client.close()
            raise RuntimeError("grok relay is unavailable") from error
        self._socket = client
        self._unregisters = [
            facade.register_handler(ControlKind.SUBMIT_PROMPT, self.submit_prompt),
            facade.register_handler(ControlKind.INTERRUPT, self.interrupt),
            facade.register_handler(ControlKind.APPROVE, self.approve),
            facade.register_handler(ControlKind.REJECT, self.reject),
            facade.register_handler(ControlKind.CONFIGURE_SESSION, self.configure_session),
            facade.register_catalog_provider(self.session_catalog),
        ]
        Thread(target=self._read, name="deskhelm-grok-relay", daemon=True).start()

    def close(self) -> None:
        self._closed = True
        client = self._socket
        self._socket = None
        if client is not None:
            client.close()
        for callback in reversed(self._unregisters):
            callback()
        self._unregisters.clear()

    def submit_prompt(self, command: ControlCommand) -> None:
        self._wait_until_ready()
        text = command.payload.text
        self._emit(self._decoder.user_message(text, command.command_id))
        self._write(self._decoder.next_prompt(text))

    def interrupt(self, command: ControlCommand) -> None:
        del command
        self._wait_until_ready()
        self._write(self._decoder.cancel_request())

    def approve(self, command: ControlCommand) -> None:
        self._write(self._decoder.permission_result(command.payload.request_id, allow=True))

    def reject(self, command: ControlCommand) -> None:
        self._write(self._decoder.permission_result(command.payload.request_id, allow=False))

    def configure_session(self, command: ControlCommand) -> None:
        payload = command.payload
        if payload.effort_set and payload.reasoning_effort:
            raise RuntimeError("reasoning override is not available")
        if payload.model_set and payload.model:
            current = self._decoder.catalog.get("model")
            if payload.model != current:
                raise RuntimeError("model override is not available")

    def session_catalog(self, agent_id: str, session_id: str, project_id: str) -> dict:
        del agent_id, session_id, project_id
        return dict(self._decoder.catalog)

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + 30
        while not self._decoder.session_id_vendor:
            if self._closed or time.monotonic() >= deadline:
                raise RuntimeError("grok session is not ready")
            time.sleep(0.05)

    def _read(self) -> None:
        client = self._socket
        if client is None:
            return
        reader = client.makefile("rb")
        try:
            while not self._closed:
                line = reader.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 1024 * 1024:
                    break
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    for event in self._decoder.feed(value):
                        self._emit(event)
        finally:
            reader.close()
            for event in self._decoder.stream_ended():
                self._emit(event)

    def _write(self, value: dict) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(payload) > 1024 * 1024:
            raise RuntimeError("grok control line is too long")
        with self._write_lock:
            client = self._socket
            if client is None or self._closed:
                raise RuntimeError("grok relay is closed")
            try:
                client.sendall(payload)
            except OSError as error:
                raise RuntimeError("grok relay is closed") from error

    def _emit(self, event) -> None:
        publish = self._publish
        if publish is not None and not self._closed:
            publish(event)
