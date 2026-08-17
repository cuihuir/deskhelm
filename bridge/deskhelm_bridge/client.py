from __future__ import annotations

from pathlib import Path
import socket

from .event import AgentEvent, PROTOCOL_VERSION, ProtocolError
from .transport import (
    AGENT_EVENT_MESSAGE_TYPE,
    AGENT_EVENT_V1_CAPABILITY,
    PROTOCOL_ERROR_MESSAGE_TYPE,
    SERVER_HELLO_MESSAGE_TYPE,
    ClientHello,
    ClientRole,
    ProtocolErrorFrame,
    ServerHello,
    decode_json_object,
    encode_frame,
    read_frame,
)


def send_event(event: AgentEvent, socket_path: Path) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        try:
            client.connect(str(socket_path))
        except FileNotFoundError as error:
            raise ConnectionError(f"bridge is not running at {socket_path}") from error
        client.sendall((event.to_json() + "\n").encode())


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
