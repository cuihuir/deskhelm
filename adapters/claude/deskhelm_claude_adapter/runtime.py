from __future__ import annotations

import json
import socket
from threading import Lock, Thread
import uuid

from deskhelm_bridge.control import ControlCommand, ControlKind, ControlUndelivered
from deskhelm_bridge.runtime_directory import RuntimeAttachSpec

from .stream import ClaudeEventDecoder


class ClaudeRelayRuntime:
    """Read one Claude stream relay and publish DeskHelm events."""

    def __init__(self, spec: RuntimeAttachSpec) -> None:
        if spec.relay_socket is None:
            raise ValueError("relay socket is required")
        self.relay_socket = spec.relay_socket
        self._decoder = ClaudeEventDecoder(spec.session, spec.source_version)
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
            raise RuntimeError("claude relay is unavailable") from error
        self._socket = client
        self._unregisters = [
            facade.register_handler(ControlKind.SUBMIT_PROMPT, self.submit_prompt),
            facade.register_handler(ControlKind.INTERRUPT, self.interrupt),
            facade.register_handler(ControlKind.APPROVE, self.approve),
            facade.register_handler(ControlKind.REJECT, self.reject),
            facade.register_handler(ControlKind.CONFIGURE_SESSION, self.configure_session),
            facade.register_catalog_provider(self.session_catalog),
        ]
        Thread(target=self._read, name="deskhelm-claude-relay", daemon=True).start()

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
        self._require_open()
        text = command.payload.text
        self._decoder.note_outbound_prompt(text)
        self._emit(self._decoder.user_message(text, command.command_id))
        self._write(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                },
            }
        )

    def interrupt(self, command: ControlCommand) -> None:
        del command
        self._write(
            {
                "type": "control_request",
                "request_id": uuid.uuid4().hex,
                "request": {"subtype": "interrupt"},
            }
        )

    def approve(self, command: ControlCommand) -> None:
        self._decide(command, allow=True)

    def reject(self, command: ControlCommand) -> None:
        self._decide(command, allow=False)

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

    def _decide(self, command: ControlCommand, *, allow: bool) -> None:
        self._require_open()
        request_id = command.payload.request_id
        pending = self._decoder.pending_approvals.pop(request_id, None)
        if pending is None:
            raise RuntimeError("approval request is not pending")
        response: dict = {"behavior": "allow" if allow else "deny"}
        if allow:
            response["updatedInput"] = pending.get("input") or {}
        else:
            response["message"] = "denied"
        self._write(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": response,
                },
            }
        )

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
            raise RuntimeError("claude control line is too long")
        with self._write_lock:
            client = self._socket
            if client is None or self._closed:
                raise ControlUndelivered("claude relay is closed")
            try:
                client.sendall(payload)
            except OSError as error:
                # Part of the line may have been written, so this is ambiguous.
                raise RuntimeError("claude relay is closed") from error

    def _require_open(self) -> None:
        if self._socket is None or self._closed:
            raise ControlUndelivered("claude relay is closed")

    def _emit(self, event) -> None:
        publish = self._publish
        if publish is not None and not self._closed:
            publish(event)
