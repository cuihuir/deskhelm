from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import socket
from threading import Event, Lock, RLock, Semaphore
from typing import BinaryIO, TextIO
import uuid

from .display import SlotPanel
from .event import AgentEvent, PROTOCOL_VERSION, ProtocolError
from .session_registry import SessionRegistry
from .state_store import StateStore
from .transport import (
    AGENT_EVENT_MESSAGE_TYPE,
    AGENT_EVENT_V1_CAPABILITY,
    CLIENT_HELLO_MESSAGE_TYPE,
    MAX_FRAME_BYTES,
    ClientHello,
    ClientRole,
    ProtocolErrorFrame,
    ServerHello,
    decode_json_object,
    encode_frame,
    read_frame,
)


DEFAULT_MAX_CONNECTIONS = 16
ACCEPT_POLL_SECONDS = 0.1
MAX_ERROR_MESSAGE_CHARS = 512


@dataclass(slots=True)
class _BridgeRuntime:
    state_store: StateStore
    session_registry: SessionRegistry
    stream: TextIO
    max_events: int | None
    stream_id: str
    max_connections: int
    stop: Event = field(default_factory=Event)
    received: int = 0
    _event_lock: Lock = field(default_factory=Lock, repr=False)
    _output_lock: Lock = field(default_factory=Lock, repr=False)

    def process(self, event: AgentEvent) -> None:
        with self._event_lock:
            if self.max_events is not None and self.received >= self.max_events:
                return
            self.session_registry.observe(event)
            with self._output_lock:
                self.state_store.update(event)
            self.received += 1
            if self.max_events is not None and self.received >= self.max_events:
                self.stop.set()

    def report_error(self, error: Exception) -> None:
        with self._output_lock:
            self.stream.write(f"error={str(error)[:MAX_ERROR_MESSAGE_CHARS]}\n")
            self.stream.flush()


def run_bridge(
    socket_path: Path,
    slot_count: int,
    stream: TextIO,
    *,
    color: bool = True,
    live: bool = True,
    max_events: int | None = None,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
) -> int:
    if max_connections < 1:
        raise ValueError("max_connections must be at least 1")
    if max_events is not None and max_events < 1:
        raise ValueError("max_events must be at least 1")

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    state_store = StateStore(slot_count=slot_count)
    session_registry = SessionRegistry(slot_count=slot_count)
    panel = SlotPanel(stream=stream, color=color, live=live)
    state_store.subscribe(panel.on_state_change)
    panel.render(state_store.snapshot())
    runtime = _BridgeRuntime(
        state_store=state_store,
        session_registry=session_registry,
        stream=stream,
        max_events=max_events,
        stream_id=str(uuid.uuid4()),
        max_connections=max_connections,
    )
    permits = Semaphore(max_connections)
    active_connections: set[socket.socket] = set()
    active_lock = RLock()

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            socket_path.chmod(0o600)
            server.listen(max_connections)
            server.settimeout(ACCEPT_POLL_SECONDS)
            executor = ThreadPoolExecutor(
                max_workers=max_connections, thread_name_prefix="deskhelm-connection"
            )
            try:
                while not runtime.stop.is_set():
                    if not permits.acquire(timeout=ACCEPT_POLL_SECONDS):
                        continue
                    try:
                        connection, _ = server.accept()
                    except TimeoutError:
                        permits.release()
                        continue
                    with active_lock:
                        active_connections.add(connection)
                    try:
                        executor.submit(
                            _handle_connection,
                            connection,
                            runtime,
                            permits,
                            active_connections,
                            active_lock,
                        )
                    except BaseException:
                        with active_lock:
                            active_connections.discard(connection)
                        connection.close()
                        permits.release()
                        raise
            finally:
                runtime.stop.set()
                with active_lock:
                    connections = tuple(active_connections)
                for connection in connections:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                executor.shutdown(wait=True, cancel_futures=True)
    finally:
        socket_path.unlink(missing_ok=True)
    return runtime.received


def _handle_connection(
    connection: socket.socket,
    runtime: _BridgeRuntime,
    permits: Semaphore,
    active_connections: set[socket.socket],
    active_lock: RLock,
) -> None:
    try:
        with connection, connection.makefile("rb") as reader:
            first_frame = read_frame(reader)
            if first_frame is None:
                return
            first_value = decode_json_object(first_frame)
            if first_value.get("message_type") == CLIENT_HELLO_MESSAGE_TYPE:
                _handle_negotiated_connection(connection, reader, first_value, runtime)
            elif "message_type" not in first_value:
                _handle_legacy_connection(reader, first_value, runtime)
            else:
                raise ProtocolError("first frame must be client_hello or AgentEvent v1")
    except (OSError, ProtocolError, ValueError) as error:
        if not runtime.stop.is_set():
            runtime.report_error(error)
    finally:
        with active_lock:
            active_connections.discard(connection)
        permits.release()


def _handle_legacy_connection(
    reader: BinaryIO, first_value: dict[str, object], runtime: _BridgeRuntime
) -> None:
    runtime.process(AgentEvent.from_dict(first_value))
    while not runtime.stop.is_set():
        frame = read_frame(reader)
        if frame is None:
            return
        try:
            runtime.process(AgentEvent.from_dict(decode_json_object(frame)))
        except (ProtocolError, ValueError) as error:
            runtime.report_error(error)


def _handle_negotiated_connection(
    connection: socket.socket,
    reader: BinaryIO,
    first_value: dict[str, object],
    runtime: _BridgeRuntime,
) -> None:
    try:
        hello = ClientHello.from_dict(first_value)
        if PROTOCOL_VERSION not in hello.supported_versions:
            raise _NegotiationError("version_unavailable", "no supported version overlaps")
        if hello.role is not ClientRole.PUBLISHER:
            raise _NegotiationError(
                "role_unavailable", f"{hello.role.value} connections are not enabled yet"
            )
        if AGENT_EVENT_V1_CAPABILITY not in hello.capabilities:
            raise _NegotiationError(
                "capability_unavailable",
                f"publisher must request {AGENT_EVENT_V1_CAPABILITY}",
            )
        response = ServerHello(
            selected_version=PROTOCOL_VERSION,
            accepted_capabilities=(AGENT_EVENT_V1_CAPABILITY,),
            stream_id=runtime.stream_id,
            max_frame_bytes=MAX_FRAME_BYTES,
            max_connections=runtime.max_connections,
        )
        connection.sendall(encode_frame(response.to_dict()))
    except _NegotiationError as error:
        _send_protocol_error(connection, error.code, str(error))
        return
    except ProtocolError as error:
        _send_protocol_error(connection, "invalid_hello", str(error))
        return

    while not runtime.stop.is_set():
        try:
            frame = read_frame(reader)
            if frame is None:
                return
            value = decode_json_object(frame)
            message_type = value.get("message_type")
            if message_type != AGENT_EVENT_MESSAGE_TYPE:
                raise ProtocolError(f"publisher cannot send message_type {message_type}")
            event_value = dict(value)
            del event_value["message_type"]
            runtime.process(AgentEvent.from_dict(event_value))
        except ProtocolError as error:
            _send_protocol_error(connection, "invalid_frame", str(error))
            return


def _send_protocol_error(connection: socket.socket, code: str, message: str) -> None:
    try:
        error = ProtocolErrorFrame(code, message[:MAX_ERROR_MESSAGE_CHARS])
        connection.sendall(encode_frame(error.to_dict()))
    except (OSError, ProtocolError):
        pass


class _NegotiationError(ProtocolError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
