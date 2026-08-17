from __future__ import annotations

from pathlib import Path
import socket
from typing import TextIO

from .display import SlotPanel
from .event import AgentEvent, ProtocolError
from .session_registry import SessionRegistry
from .state_store import StateStore


def run_bridge(
    socket_path: Path,
    slot_count: int,
    stream: TextIO,
    *,
    color: bool = True,
    live: bool = True,
    max_events: int | None = None,
) -> int:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    state_store = StateStore(slot_count=slot_count)
    session_registry = SessionRegistry(slot_count=slot_count)
    panel = SlotPanel(stream=stream, color=color, live=live)
    state_store.subscribe(panel.on_state_change)
    panel.render(state_store.snapshot())
    received = 0

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            socket_path.chmod(0o600)
            server.listen()
            while max_events is None or received < max_events:
                connection, _ = server.accept()
                with connection, connection.makefile("r", encoding="utf-8") as reader:
                    for line in reader:
                        try:
                            event = AgentEvent.from_json(line)
                            session_registry.observe(event)
                            state_store.update(event)
                            received += 1
                        except (ProtocolError, ValueError) as error:
                            stream.write(f"error={error}\n")
                            stream.flush()
                        if max_events is not None and received >= max_events:
                            break
    finally:
        socket_path.unlink(missing_ok=True)
    return received
