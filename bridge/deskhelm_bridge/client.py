from __future__ import annotations

from pathlib import Path
import select
import socket
from threading import Event
from typing import BinaryIO, Iterator

from .adapter import AdapterSessionEvent, AdapterSessionResult
from .event import AgentEvent, PROTOCOL_VERSION, ProtocolError
from .control import ControlCommand
from .control_result import ControlResult
from .transport import (
    ADAPTER_SESSION_V1_CAPABILITY,
    AGENT_EVENT_MESSAGE_TYPE,
    AGENT_EVENT_V1_CAPABILITY,
    PROTOCOL_ERROR_MESSAGE_TYPE,
    SERVER_HELLO_MESSAGE_TYPE,
    ClientHello,
    ClientRole,
    CONTROL_COMMAND_V1_CAPABILITY,
    INTERACTION_SUBSCRIPTION_V1_CAPABILITY,
    ProtocolErrorFrame,
    ServerHello,
    decode_json_object,
    encode_frame,
    read_frame,
)


_CODEX_FAILURE_STATUS_MESSAGES = {
    "codex_connection_lost": "Codex 控制连接中断（codex_connection_lost）",
    "codex_control_failed": "Codex 控制请求被拒绝（codex_control_failed）",
    "codex_control_timeout": "Codex 控制请求超时（codex_control_timeout）",
    "codex_thread_ambiguous": "检测到多个 Codex 会话（codex_thread_ambiguous）",
    "codex_turn_failed": "Codex turn 执行失败（codex_turn_failed）",
    "interrupted": "Codex turn 已中断（interrupted）",
}


class AdapterSessionPublisher:
    """Hold one negotiated publisher connection for session lifecycle."""

    def __init__(
        self,
        socket_path: Path,
        *,
        client_id: str,
        additional_capabilities: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(additional_capabilities, tuple) or any(
            not isinstance(capability, str) or not capability.strip()
            for capability in additional_capabilities
        ):
            raise ValueError("additional publisher capabilities are invalid")
        if (
            ADAPTER_SESSION_V1_CAPABILITY in additional_capabilities
            or len(set(additional_capabilities)) != len(additional_capabilities)
        ):
            raise ValueError("additional publisher capabilities are invalid")
        self.socket_path = socket_path
        self.client_id = client_id
        self.capabilities = (
            ADAPTER_SESSION_V1_CAPABILITY,
            *additional_capabilities,
        )
        self._client: socket.socket | None = None
        self._reader: BinaryIO | None = None

    def connect(self) -> ServerHello:
        if self._client is not None:
            raise RuntimeError("adapter session publisher is already connected")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        reader = None
        try:
            client.connect(str(self.socket_path))
            reader = client.makefile("rb")
            hello = ClientHello(
                client_id=self.client_id,
                role=ClientRole.PUBLISHER,
                supported_versions=(PROTOCOL_VERSION,),
                capabilities=self.capabilities,
            )
            client.sendall(encode_frame(hello.to_dict()))
            response = _read_negotiated_response(reader, "server_hello")
            server_hello = ServerHello.from_dict(response)
            if any(
                capability not in server_hello.accepted_capabilities
                for capability in self.capabilities
            ):
                raise ProtocolError(
                    "bridge did not accept requested publisher capabilities"
                )
        except BaseException:
            if reader is not None:
                reader.close()
            client.close()
            raise
        self._client = client
        self._reader = reader
        return server_hello

    def publish(self, event: AdapterSessionEvent) -> AdapterSessionResult:
        if self._client is None or self._reader is None:
            raise RuntimeError("adapter session publisher is not connected")
        self._client.sendall(encode_frame(event.to_dict()))
        response = _read_negotiated_response(
            self._reader,
            "adapter_session_result",
        )
        return AdapterSessionResult.from_dict(response)

    def close(self) -> None:
        reader = self._reader
        client = self._client
        self._reader = None
        self._client = None
        if reader is not None:
            reader.close()
        if client is not None:
            client.close()

    def __enter__(self) -> AdapterSessionPublisher:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def iter_voice_status_messages(
    socket_path: Path,
    *,
    client_id: str,
    agent_id: str,
    session_id: str,
    project_id: str,
    stop: Event,
) -> Iterator[str]:
    """Yield fixed, target-scoped voice and Codex failure guidance."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(socket_path))
        with client.makefile("rb") as reader:
            hello = ClientHello(
                client_id=client_id,
                role=ClientRole.SUBSCRIBER,
                supported_versions=(PROTOCOL_VERSION,),
                capabilities=(INTERACTION_SUBSCRIPTION_V1_CAPABILITY,),
            )
            client.sendall(encode_frame(hello.to_dict()))
            response = decode_json_object(read_frame(reader) or b"")
            if response.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
                raise ConnectionError(ProtocolErrorFrame.from_dict(response).message)
            server_hello = ServerHello.from_dict(response)
            if INTERACTION_SUBSCRIPTION_V1_CAPABILITY not in server_hello.accepted_capabilities:
                raise ProtocolError("bridge did not accept interaction_subscription_v1")
            read_frame(reader)  # Live-only subscription start marker.
            while not stop.is_set():
                readable, _, _ = select.select([client], [], [], 0.25)
                if not readable:
                    continue
                frame = read_frame(reader)
                if frame is None:
                    return
                value = decode_json_object(frame)
                if value.get("message_type") != "interaction_update":
                    continue
                event = value.get("event")
                message = _voice_status_message(
                    event,
                    agent_id=agent_id,
                    session_id=session_id,
                    project_id=project_id,
                )
                if message is not None:
                    yield message


def _voice_status_message(
    event: object,
    *,
    agent_id: str,
    session_id: str,
    project_id: str,
) -> str | None:
    if not isinstance(event, dict) or any(
        event.get(name) != expected
        for name, expected in (
            ("agent_id", agent_id),
            ("session_id", session_id),
            ("project_id", project_id),
        )
    ):
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    if (
        event.get("source") == "voice-gateway"
        and event.get("kind") == "message"
        and payload.get("role") == "system"
        and payload.get("phase") == "complete"
    ):
        text = payload.get("text")
        return text if isinstance(text, str) and text else None
    if (
        event.get("source") == "codex-app-server"
        and event.get("kind") == "task_failed"
    ):
        return _CODEX_FAILURE_STATUS_MESSAGES.get(payload.get("error_code"))
    return None


def send_control_command(
    command: ControlCommand, socket_path: Path, *, client_id: str
) -> ControlResult:
    if command.issued_by != client_id:
        raise ValueError("control command issuer must match client_id")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        try:
            client.connect(str(socket_path))
        except FileNotFoundError as error:
            raise ConnectionError(f"bridge is not running at {socket_path}") from error
        with client.makefile("rb") as reader:
            hello = ClientHello(
                client_id=client_id,
                role=ClientRole.CONTROLLER,
                supported_versions=(PROTOCOL_VERSION,),
                capabilities=(CONTROL_COMMAND_V1_CAPABILITY,),
            )
            client.sendall(encode_frame(hello.to_dict()))
            response = decode_json_object(read_frame(reader) or b"")
            if response.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
                raise ConnectionError(ProtocolErrorFrame.from_dict(response).message)
            server_hello = ServerHello.from_dict(response)
            if CONTROL_COMMAND_V1_CAPABILITY not in server_hello.accepted_capabilities:
                raise ProtocolError("bridge did not accept control_command_v1")
            client.sendall(encode_frame(command.to_dict()))
            result = decode_json_object(read_frame(reader) or b"")
            if result.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
                raise ConnectionError(ProtocolErrorFrame.from_dict(result).message)
            return ControlResult.from_dict(result)


def send_event(event: AgentEvent, socket_path: Path) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        try:
            client.connect(str(socket_path))
        except FileNotFoundError as error:
            raise ConnectionError(f"bridge is not running at {socket_path}") from error
        client.sendall((event.to_json() + "\n").encode())


def _read_negotiated_response(reader: BinaryIO, expected_type: str) -> dict:
    frame = read_frame(reader)
    if frame is None:
        raise ConnectionError(f"bridge closed before {expected_type}")
    response = decode_json_object(frame)
    if response.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
        error = ProtocolErrorFrame.from_dict(response)
        raise ConnectionError(f"bridge rejected request: {error.code}: {error.message}")
    if response.get("message_type") != expected_type:
        raise ProtocolError(f"bridge response must be {expected_type}")
    return response


def send_negotiated_event(
    event: AgentEvent, socket_path: Path, *, client_id: str
) -> ServerHello:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        try:
            client.connect(str(socket_path))
        except FileNotFoundError as error:
            raise ConnectionError(f"bridge is not running at {socket_path}") from error
        with client.makefile("rb") as reader:
            hello = ClientHello(
                client_id=client_id,
                role=ClientRole.PUBLISHER,
                supported_versions=(PROTOCOL_VERSION,),
                capabilities=(AGENT_EVENT_V1_CAPABILITY,),
            )
            client.sendall(encode_frame(hello.to_dict()))
            response_frame = read_frame(reader)
            if response_frame is None:
                raise ConnectionError("bridge closed before server_hello")
            response = decode_json_object(response_frame)
            if response.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
                error = ProtocolErrorFrame.from_dict(response)
                raise ConnectionError(
                    f"bridge rejected negotiation: {error.code}: {error.message}"
                )
            if response.get("message_type") != SERVER_HELLO_MESSAGE_TYPE:
                raise ProtocolError("bridge response must be server_hello")
            server_hello = ServerHello.from_dict(response)
            if AGENT_EVENT_V1_CAPABILITY not in server_hello.accepted_capabilities:
                raise ProtocolError("bridge did not accept agent_event_v1")
            event_frame = event.to_dict()
            event_frame["message_type"] = AGENT_EVENT_MESSAGE_TYPE
            client.sendall(encode_frame(event_frame))
            return server_hello
