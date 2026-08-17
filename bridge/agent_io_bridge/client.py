from __future__ import annotations

from pathlib import Path
import socket

from .event import AgentEvent


def send_event(event: AgentEvent, socket_path: Path) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        try:
            client.connect(str(socket_path))
        except FileNotFoundError as error:
            raise ConnectionError(f"bridge is not running at {socket_path}") from error
        client.sendall((event.to_json() + "\n").encode())
