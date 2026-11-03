from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import select
import socket
from threading import Event
from typing import BinaryIO

from .adapter import AdapterSessionEvent, AdapterSessionResult
from .event import AgentEvent, PROTOCOL_VERSION, ProtocolError
from .control import ControlCommand
from .control_result import ControlResult
from .runtime_directory import (
    ATTACH_STATUSES,
    DETACH_STATUSES,
    AgentRuntimeDirectoryStatus,
)
from .session_registry import SessionKey
from .transport import (
    ADAPTER_SESSION_V1_CAPABILITY,
    AGENT_EVENT_MESSAGE_TYPE,
    AGENT_EVENT_V1_CAPABILITY,
    PROTOCOL_ERROR_MESSAGE_TYPE,
    SERVER_HELLO_MESSAGE_TYPE,
    ClientHello,
    ClientRole,
    AGENT_RUNTIME_ATTACH_MESSAGE_TYPE,
    AGENT_RUNTIME_ATTACH_RESULT_MESSAGE_TYPE,
    AGENT_RUNTIME_ATTACH_V1_CAPABILITY,
    AGENT_RUNTIME_DETACH_MESSAGE_TYPE,
    AGENT_RUNTIME_DETACH_RESULT_MESSAGE_TYPE,
    AGENT_RUNTIME_DIRECTORY_QUERY_MESSAGE_TYPE,
    AGENT_RUNTIME_DIRECTORY_STATUS_MESSAGE_TYPE,
    CONTROL_COMMAND_V1_CAPABILITY,
    FOCUS_QUERY_MESSAGE_TYPE,
    FOCUS_QUERY_RESULT_MESSAGE_TYPE,
    SESSION_QUERY_MESSAGE_TYPE,
    SESSION_QUERY_RESULT_MESSAGE_TYPE,
    SESSION_CATALOG_QUERY_MESSAGE_TYPE,
    SESSION_CATALOG_RESULT_MESSAGE_TYPE,
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
    agent_id: str | None,
    session_id: str | None,
    project_id: str | None,
    stop: Event,
) -> Iterator[str]:
    """Yield fixed voice guidance, optionally limited to one session."""
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
    agent_id: str | None,
    session_id: str | None,
    project_id: str | None,
) -> str | None:
    if not isinstance(event, dict) or any(
        expected is not None and event.get(name) != expected
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


def get_focused_session(
    socket_path: Path, *, client_id: str
) -> SessionKey | None:
    """Read the Bridge-local focus without creating an untargeted control."""
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
            client.sendall(
                encode_frame(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "message_type": FOCUS_QUERY_MESSAGE_TYPE,
                    }
                )
            )
            result = decode_json_object(read_frame(reader) or b"")
            if result.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
                raise ConnectionError(ProtocolErrorFrame.from_dict(result).message)
            if result.get("message_type") != FOCUS_QUERY_RESULT_MESSAGE_TYPE:
                raise ProtocolError("bridge returned an invalid focus query result")
            if result.get("protocol_version") != PROTOCOL_VERSION:
                raise ProtocolError("focus query result protocol version is invalid")
            focused = result.get("focused_session")
            if focused is None:
                return None
            if not isinstance(focused, dict):
                raise ProtocolError("focused_session must be an object or null")
            try:
                return SessionKey(
                    agent_id=focused["agent_id"],
                    session_id=focused["session_id"],
                    project_id=focused["project_id"],
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ProtocolError("focused_session is invalid") from error


def query_agent_runtime_directory(
    socket_path: Path, *, client_id: str
) -> AgentRuntimeDirectoryStatus:
    """Read whether the user bridge accepts attached sessions and voice."""
    with _controller(
        socket_path,
        client_id=client_id,
        capabilities=(
            CONTROL_COMMAND_V1_CAPABILITY,
            AGENT_RUNTIME_ATTACH_V1_CAPABILITY,
        ),
    ) as (client, reader):
        client.sendall(
            encode_frame(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "message_type": AGENT_RUNTIME_DIRECTORY_QUERY_MESSAGE_TYPE,
                }
            )
        )
        result = _read_negotiated_response(
            reader, AGENT_RUNTIME_DIRECTORY_STATUS_MESSAGE_TYPE
        )
    return _directory_status(result)


def attach_relay_runtime(
    socket_path: Path,
    *,
    client_id: str,
    agent_id: str,
    session_id: str,
    project_id: str,
    source_version: str,
    runtime_kind: str,
    relay_socket: Path,
) -> str:
    """Attach one Claude or Grok relay. The result is a fixed status code."""
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "message_type": AGENT_RUNTIME_ATTACH_MESSAGE_TYPE,
        "agent_id": agent_id,
        "session_id": session_id,
        "project_id": project_id,
        "source_version": source_version,
        "runtime_kind": runtime_kind,
        "relay_socket": str(relay_socket),
    }
    return _runtime_status(
        socket_path,
        client_id=client_id,
        payload=payload,
        expected_type=AGENT_RUNTIME_ATTACH_RESULT_MESSAGE_TYPE,
        allowed_statuses=ATTACH_STATUSES,
        agent_id=agent_id,
        session_id=session_id,
        project_id=project_id,
    )


def attach_agent_runtime(
    socket_path: Path,
    *,
    client_id: str,
    agent_id: str,
    session_id: str,
    project_id: str,
    source_version: str,
    app_server_socket: Path,
    tui_proxy_socket: Path,
) -> str:
    """Attach one in-process runtime. The result is a fixed status code."""
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "message_type": AGENT_RUNTIME_ATTACH_MESSAGE_TYPE,
        "agent_id": agent_id,
        "session_id": session_id,
        "project_id": project_id,
        "source_version": source_version,
        "app_server_socket": str(app_server_socket),
        "tui_proxy_socket": str(tui_proxy_socket),
    }
    return _runtime_status(
        socket_path,
        client_id=client_id,
        payload=payload,
        expected_type=AGENT_RUNTIME_ATTACH_RESULT_MESSAGE_TYPE,
        allowed_statuses=ATTACH_STATUSES,
        agent_id=agent_id,
        session_id=session_id,
        project_id=project_id,
    )


def detach_agent_runtime(
    socket_path: Path,
    *,
    client_id: str,
    agent_id: str,
    session_id: str,
    project_id: str,
) -> str:
    """Detach one runtime. The result is a fixed status code."""
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "message_type": AGENT_RUNTIME_DETACH_MESSAGE_TYPE,
        "agent_id": agent_id,
        "session_id": session_id,
        "project_id": project_id,
    }
    return _runtime_status(
        socket_path,
        client_id=client_id,
        payload=payload,
        expected_type=AGENT_RUNTIME_DETACH_RESULT_MESSAGE_TYPE,
        allowed_statuses=DETACH_STATUSES,
        agent_id=agent_id,
        session_id=session_id,
        project_id=project_id,
    )


def send_event(event: AgentEvent, socket_path: Path) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        try:
            client.connect(str(socket_path))
        except FileNotFoundError as error:
            raise ConnectionError(f"bridge is not running at {socket_path}") from error
        client.sendall((event.to_json() + "\n").encode())


@contextmanager
def _controller(
    socket_path: Path,
    *,
    client_id: str,
    capabilities: tuple[str, ...],
) -> Iterator[tuple[socket.socket, BinaryIO]]:
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
                capabilities=capabilities,
            )
            client.sendall(encode_frame(hello.to_dict()))
            response = decode_json_object(read_frame(reader) or b"")
            if response.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
                raise ConnectionError(ProtocolErrorFrame.from_dict(response).message)
            server_hello = ServerHello.from_dict(response)
            missing = [
                capability
                for capability in capabilities
                if capability not in server_hello.accepted_capabilities
            ]
            if missing:
                raise ProtocolError(
                    "bridge did not accept " + ", ".join(missing)
                )
            yield client, reader


def _runtime_status(
    socket_path: Path,
    *,
    client_id: str,
    payload: dict[str, object],
    expected_type: str,
    allowed_statuses: frozenset[str],
    agent_id: str,
    session_id: str,
    project_id: str,
) -> str:
    with _controller(
        socket_path,
        client_id=client_id,
        capabilities=(
            CONTROL_COMMAND_V1_CAPABILITY,
            AGENT_RUNTIME_ATTACH_V1_CAPABILITY,
        ),
    ) as (client, reader):
        client.sendall(encode_frame(payload))
        result = _read_negotiated_response(reader, expected_type)
    status = result.get("status")
    if not isinstance(status, str) or status not in allowed_statuses:
        raise ProtocolError("agent runtime result is invalid")
    if any(
        name in result
        for name in (
            "app_server_socket",
            "tui_proxy_socket",
            "socket",
            "path",
            "cwd",
            "working_directory",
        )
    ):
        raise ProtocolError("agent runtime result is invalid")
    if status != "invalid" and (
        result.get("agent_id") != agent_id
        or result.get("session_id") != session_id
        or result.get("project_id") != project_id
    ):
        raise ProtocolError("agent runtime result target is invalid")
    return status


def _directory_status(value: dict[str, object]) -> AgentRuntimeDirectoryStatus:
    if value.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("agent runtime directory status is invalid")
    enabled = value.get("enabled")
    voice_enabled = value.get("voice_enabled")
    capacity = value.get("capacity")
    attached = value.get("attached")
    if not isinstance(enabled, bool) or not isinstance(voice_enabled, bool):
        raise ProtocolError("agent runtime directory status is invalid")
    if (
        not isinstance(capacity, int)
        or isinstance(capacity, bool)
        or capacity < 0
        or not isinstance(attached, int)
        or isinstance(attached, bool)
        or attached < 0
    ):
        raise ProtocolError("agent runtime directory status is invalid")
    if any(
        name in value
        for name in ("app_server_socket", "tui_proxy_socket", "socket", "path", "cwd")
    ):
        raise ProtocolError("agent runtime directory status is invalid")
    return AgentRuntimeDirectoryStatus(
        enabled=enabled,
        voice_enabled=voice_enabled,
        capacity=capacity,
        attached=attached,
    )


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


def get_session_snapshot(
    socket_path: Path, *, client_id: str
) -> dict:
    """Read bounded session metadata for local operator clients."""
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
            client.sendall(
                encode_frame(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "message_type": SESSION_QUERY_MESSAGE_TYPE,
                    }
                )
            )
            result = decode_json_object(read_frame(reader) or b"")
            if result.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
                raise ConnectionError(ProtocolErrorFrame.from_dict(result).message)
            if result.get("message_type") != SESSION_QUERY_RESULT_MESSAGE_TYPE:
                raise ProtocolError("bridge returned an invalid session query result")
            sessions = result.get("sessions")
            if not isinstance(sessions, list):
                raise ProtocolError("session query result sessions must be an array")
            return result


def get_session_catalog(
    socket_path: Path,
    *,
    client_id: str,
    agent_id: str,
    session_id: str,
    project_id: str,
) -> dict:
    """Read one session's bounded model, MCP, and skill catalog."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(25)
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
            client.sendall(
                encode_frame(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "message_type": SESSION_CATALOG_QUERY_MESSAGE_TYPE,
                        "agent_id": agent_id,
                        "session_id": session_id,
                        "project_id": project_id,
                    }
                )
            )
            result = decode_json_object(read_frame(reader) or b"")
            if result.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
                raise ConnectionError(ProtocolErrorFrame.from_dict(result).message)
            if result.get("message_type") != SESSION_CATALOG_RESULT_MESSAGE_TYPE:
                raise ProtocolError("bridge returned an invalid session catalog result")
            if result.get("agent_id") != agent_id or result.get("session_id") != session_id:
                raise ProtocolError("session catalog result targeted a different session")
            if result.get("project_id") != project_id:
                raise ProtocolError("session catalog result targeted a different session")
            return result
