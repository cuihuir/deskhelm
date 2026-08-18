from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import select
import socket
from threading import Event, Lock, RLock, Semaphore
import time
from typing import BinaryIO, TextIO
import uuid

from .adapter import (
    ADAPTER_SESSION_MESSAGE_TYPE,
    AdapterCapability,
    AdapterSessionEvent,
    AdapterSessionResult,
)
from .adapter_registry import AdapterRegistry
from .control import CONTROL_COMMAND_MESSAGE_TYPE, ControlCommand
from .control_router import ControlRouter
from .display import SlotPanel
from .event import AgentEvent, PROTOCOL_VERSION, ProtocolError
from .interaction import INTERACTION_MESSAGE_TYPE, InteractionEvent
from .interaction_subscription import InteractionHub, InteractionSubscriberQueue
from .session_registry import SessionRegistry
from .state_store import StateStore
from .subscription import StateSubscriberQueue
from .transport import (
    ADAPTER_SESSION_V1_CAPABILITY,
    AGENT_EVENT_MESSAGE_TYPE,
    AGENT_EVENT_V1_CAPABILITY,
    CLIENT_HELLO_MESSAGE_TYPE,
    CONTROL_COMMAND_V1_CAPABILITY,
    INTERACTION_EVENT_V1_CAPABILITY,
    INTERACTION_SUBSCRIPTION_V1_CAPABILITY,
    MAX_FRAME_BYTES,
    STATE_SUBSCRIPTION_V1_CAPABILITY,
    ClientHello,
    ClientRole,
    ProtocolErrorFrame,
    ServerHello,
    decode_json_object,
    encode_frame,
    read_frame,
)


DEFAULT_MAX_CONNECTIONS = 16
DEFAULT_SUBSCRIBER_QUEUE_FRAMES = 8
DEFAULT_CONTROL_IDEMPOTENCY_ENTRIES = 1024
DEFAULT_CONTROL_IDEMPOTENCY_RETENTION_MS = 300_000
DEFAULT_CONTROL_APPROVAL_RECORDS = 1024
ACCEPT_POLL_SECONDS = 0.1
HANDSHAKE_TIMEOUT_SECONDS = 2.0
SUBSCRIBER_POLL_SECONDS = 0.1
SUBSCRIBER_WRITE_TIMEOUT_SECONDS = 2.0
CONTROLLER_WRITE_TIMEOUT_SECONDS = 2.0
MAX_ERROR_MESSAGE_CHARS = 512


@dataclass(slots=True)
class _BridgeRuntime:
    state_store: StateStore
    session_registry: SessionRegistry
    interaction_hub: InteractionHub
    control_router: ControlRouter
    adapter_registry: AdapterRegistry
    stream: TextIO
    max_events: int | None
    stream_id: str
    max_connections: int
    max_subscribers: int
    subscriber_queue_frames: int
    subscriber_permits: Semaphore
    stop: Event = field(default_factory=Event)
    received: int = 0
    _event_lock: Lock = field(default_factory=Lock, repr=False)
    _output_lock: Lock = field(default_factory=Lock, repr=False)

    def process(self, event: AgentEvent, *, observe_session: bool = True) -> None:
        with self._event_lock:
            if self.max_events is not None and self.received >= self.max_events:
                return
            if observe_session:
                self.session_registry.observe(event)
            with self._output_lock:
                self.state_store.update(event)
            self.received += 1
            if self.max_events is not None and self.received >= self.max_events:
                self.stop.set()

    def process_interaction(self, event: InteractionEvent) -> None:
        with self._event_lock:
            if self.max_events is not None and self.received >= self.max_events:
                return
            self.control_router.observe_interaction(event)
            self.interaction_hub.publish(event)
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
    max_subscribers: int | None = None,
    subscriber_queue_frames: int = DEFAULT_SUBSCRIBER_QUEUE_FRAMES,
    control_idempotency_entries: int = DEFAULT_CONTROL_IDEMPOTENCY_ENTRIES,
    control_idempotency_retention_ms: int = DEFAULT_CONTROL_IDEMPOTENCY_RETENTION_MS,
    control_approval_records: int = DEFAULT_CONTROL_APPROVAL_RECORDS,
) -> int:
    if max_connections < 1:
        raise ValueError("max_connections must be at least 1")
    if max_events is not None and max_events < 1:
        raise ValueError("max_events must be at least 1")
    if max_subscribers is None:
        max_subscribers = max_connections // 2
    if max_subscribers < 0 or max_subscribers >= max_connections:
        raise ValueError(
            "max_subscribers must be zero or greater and less than max_connections"
        )
    if subscriber_queue_frames < 1:
        raise ValueError("subscriber_queue_frames must be at least 1")
    if control_idempotency_entries < 1:
        raise ValueError("control_idempotency_entries must be at least 1")
    if control_idempotency_retention_ms < 1:
        raise ValueError("control_idempotency_retention_ms must be at least 1")
    if control_approval_records < 1:
        raise ValueError("control_approval_records must be at least 1")

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    state_store = StateStore(slot_count=slot_count)
    session_registry = SessionRegistry(slot_count=slot_count)
    control_router = ControlRouter(
        session_registry=session_registry,
        max_idempotency_entries=control_idempotency_entries,
        idempotency_retention_ms=control_idempotency_retention_ms,
        max_approval_records=control_approval_records,
    )
    adapter_registry = AdapterRegistry(session_registry=session_registry)
    panel = SlotPanel(stream=stream, color=color, live=live)
    state_store.subscribe(panel.on_state_change)
    panel.render(state_store.snapshot())
    runtime = _BridgeRuntime(
        state_store=state_store,
        session_registry=session_registry,
        interaction_hub=InteractionHub(),
        control_router=control_router,
        adapter_registry=adapter_registry,
        stream=stream,
        max_events=max_events,
        stream_id=str(uuid.uuid4()),
        max_connections=max_connections,
        max_subscribers=max_subscribers,
        subscriber_queue_frames=subscriber_queue_frames,
        subscriber_permits=Semaphore(max_subscribers),
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
        connection.settimeout(HANDSHAKE_TIMEOUT_SECONDS)
        with connection, connection.makefile("rb") as reader:
            first_frame = read_frame(reader)
            if first_frame is None:
                return
            first_value = decode_json_object(first_frame)
            if first_value.get("message_type") == CLIENT_HELLO_MESSAGE_TYPE:
                _handle_negotiated_connection(connection, reader, first_value, runtime)
            elif "message_type" not in first_value:
                connection.settimeout(None)
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
    subscriber_permit = False
    try:
        hello = ClientHello.from_dict(first_value)
        if PROTOCOL_VERSION not in hello.supported_versions:
            raise _NegotiationError("version_unavailable", "no supported version overlaps")
        if hello.role is ClientRole.PUBLISHER:
            supported_capabilities = {
                ADAPTER_SESSION_V1_CAPABILITY,
                AGENT_EVENT_V1_CAPABILITY,
                INTERACTION_EVENT_V1_CAPABILITY,
            }
            accepted_capabilities = tuple(
                capability
                for capability in hello.capabilities
                if capability in supported_capabilities
            )
            if not accepted_capabilities:
                raise _NegotiationError(
                    "capability_unavailable",
                    "publisher must request a supported publishing capability",
                )
        elif hello.role is ClientRole.SUBSCRIBER:
            supported_capabilities = {
                STATE_SUBSCRIPTION_V1_CAPABILITY,
                INTERACTION_SUBSCRIPTION_V1_CAPABILITY,
            }
            accepted_capabilities = tuple(
                capability
                for capability in hello.capabilities
                if capability in supported_capabilities
            )
            if len(accepted_capabilities) > 1:
                raise _NegotiationError(
                    "capability_conflict",
                    "subscriber must request exactly one subscription capability",
                )
            if not accepted_capabilities:
                raise _NegotiationError(
                    "capability_unavailable",
                    "subscriber must request a supported subscription capability",
                )
        elif hello.role is ClientRole.CONTROLLER:
            if CONTROL_COMMAND_V1_CAPABILITY not in hello.capabilities:
                raise _NegotiationError(
                    "capability_unavailable",
                    "controller must request control_command_v1",
                )
            accepted_capabilities = (CONTROL_COMMAND_V1_CAPABILITY,)
        else:
            raise _NegotiationError(
                "role_unavailable", f"{hello.role.value} connections are not enabled yet"
            )
        if hello.role is ClientRole.SUBSCRIBER:
            if not runtime.subscriber_permits.acquire(blocking=False):
                raise _NegotiationError(
                    "subscriber_capacity", "subscriber capacity is exhausted"
                )
            subscriber_permit = True
        response = ServerHello(
            selected_version=PROTOCOL_VERSION,
            accepted_capabilities=accepted_capabilities,
            stream_id=runtime.stream_id,
            max_frame_bytes=MAX_FRAME_BYTES,
            max_connections=runtime.max_connections,
            max_subscribers=runtime.max_subscribers,
            subscriber_queue_frames=runtime.subscriber_queue_frames,
            control_idempotency_entries=(
                runtime.control_router.max_idempotency_entries
            ),
            control_idempotency_retention_ms=(
                runtime.control_router.idempotency_retention_ms
            ),
            control_approval_records=runtime.control_router.max_approval_records,
        )
        connection.sendall(encode_frame(response.to_dict()))
    except _NegotiationError as error:
        if subscriber_permit:
            runtime.subscriber_permits.release()
        _send_protocol_error(connection, error.code, str(error))
        return
    except ProtocolError as error:
        if subscriber_permit:
            runtime.subscriber_permits.release()
        _send_protocol_error(connection, "invalid_hello", str(error))
        return
    except OSError:
        if subscriber_permit:
            runtime.subscriber_permits.release()
        raise

    if hello.role is ClientRole.SUBSCRIBER:
        try:
            if accepted_capabilities == (STATE_SUBSCRIPTION_V1_CAPABILITY,):
                _handle_state_subscriber(connection, runtime)
            else:
                _handle_interaction_subscriber(connection, runtime)
        finally:
            runtime.subscriber_permits.release()
        return

    if hello.role is ClientRole.CONTROLLER:
        connection.settimeout(None)
        _handle_controller(connection, reader, runtime, hello.client_id)
        return

    connection.settimeout(None)
    _handle_negotiated_publisher(
        connection,
        reader,
        runtime,
        frozenset(accepted_capabilities),
        owner_id=str(uuid.uuid4()),
    )


def _handle_negotiated_publisher(
    connection: socket.socket,
    reader: BinaryIO,
    runtime: _BridgeRuntime,
    capabilities: frozenset[str],
    owner_id: str,
) -> None:
    try:
        while not runtime.stop.is_set():
            frame = read_frame(reader)
            if frame is None:
                return
            value = decode_json_object(frame)
            message_type = value.get("message_type")
            if message_type == ADAPTER_SESSION_MESSAGE_TYPE:
                if ADAPTER_SESSION_V1_CAPABILITY not in capabilities:
                    raise ProtocolError("publisher did not negotiate adapter_session_v1")
                session_event = AdapterSessionEvent.from_dict(value)
                _validate_adapter_transport_capabilities(session_event, capabilities)
                slot = runtime.adapter_registry.apply(owner_id, session_event)
                result = AdapterSessionResult(
                    action=session_event.action,
                    agent_id=session_event.agent_id,
                    session_id=session_event.session_id,
                    project_id=session_event.project_id,
                    occurred_at=int(time.time() * 1000),
                    slot=slot,
                )
                connection.settimeout(CONTROLLER_WRITE_TIMEOUT_SECONDS)
                try:
                    connection.sendall(encode_frame(result.to_dict()))
                finally:
                    connection.settimeout(None)
            elif message_type == AGENT_EVENT_MESSAGE_TYPE:
                if AGENT_EVENT_V1_CAPABILITY not in capabilities:
                    raise ProtocolError("publisher did not negotiate agent_event_v1")
                event_value = dict(value)
                del event_value["message_type"]
                event = AgentEvent.from_dict(event_value)
                if ADAPTER_SESSION_V1_CAPABILITY in capabilities:
                    runtime.adapter_registry.validate_state_event(owner_id, event)
                runtime.process(
                    event,
                    observe_session=ADAPTER_SESSION_V1_CAPABILITY not in capabilities,
                )
            elif message_type == INTERACTION_MESSAGE_TYPE:
                if INTERACTION_EVENT_V1_CAPABILITY not in capabilities:
                    raise ProtocolError("publisher did not negotiate interaction_event_v1")
                event = InteractionEvent.from_dict(value)
                if ADAPTER_SESSION_V1_CAPABILITY in capabilities:
                    runtime.adapter_registry.validate_interaction_event(
                        owner_id, event
                    )
                runtime.process_interaction(event)
            else:
                raise ProtocolError(f"publisher cannot send message_type {message_type}")
    except (ProtocolError, ValueError) as error:
        _send_protocol_error(connection, "invalid_frame", str(error))
    except TimeoutError:
        return
    finally:
        if ADAPTER_SESSION_V1_CAPABILITY in capabilities:
            runtime.adapter_registry.disconnect_owner(owner_id)


def _validate_adapter_transport_capabilities(
    event: AdapterSessionEvent, transport_capabilities: frozenset[str]
) -> None:
    declared = set(event.capabilities)
    if (
        AdapterCapability.STATE_EVENTS in declared
        and AGENT_EVENT_V1_CAPABILITY not in transport_capabilities
    ):
        raise ProtocolError("state_events requires negotiated agent_event_v1")
    interaction_capabilities = {
        AdapterCapability.INTERACTION_EVENTS,
        AdapterCapability.TOOL_EVENTS,
        AdapterCapability.APPROVAL_REQUESTS,
    }
    if (
        declared.intersection(interaction_capabilities)
        and INTERACTION_EVENT_V1_CAPABILITY not in transport_capabilities
    ):
        raise ProtocolError(
            "interaction adapter capabilities require negotiated interaction_event_v1"
        )


def _handle_controller(
    connection: socket.socket,
    reader: BinaryIO,
    runtime: _BridgeRuntime,
    client_id: str,
) -> None:
    while not runtime.stop.is_set():
        try:
            frame = read_frame(reader)
            if frame is None:
                return
            value = decode_json_object(frame)
            if value.get("message_type") != CONTROL_COMMAND_MESSAGE_TYPE:
                raise ProtocolError(
                    f"controller cannot send message_type {value.get('message_type')}"
                )
            command = ControlCommand.from_dict(value)
            result = runtime.control_router.route(
                command, expected_issued_by=client_id
            )
            connection.settimeout(CONTROLLER_WRITE_TIMEOUT_SECONDS)
            try:
                connection.sendall(encode_frame(result.to_dict()))
            finally:
                connection.settimeout(None)
        except ProtocolError as error:
            _send_protocol_error(connection, "invalid_frame", str(error))
            return
        except TimeoutError:
            return


def _handle_state_subscriber(
    connection: socket.socket, runtime: _BridgeRuntime
) -> None:
    subscription = StateSubscriberQueue(
        stream_id=runtime.stream_id,
        max_queue_frames=runtime.subscriber_queue_frames,
    )
    snapshot, unsubscribe = runtime.state_store.subscribe_with_snapshot(
        subscription.enqueue
    )
    connection.settimeout(SUBSCRIBER_WRITE_TIMEOUT_SECONDS)
    try:
        try:
            snapshot_frame = encode_frame(subscription.snapshot(snapshot).to_dict())
        except ProtocolError:
            _send_protocol_error(
                connection,
                "snapshot_too_large",
                "state snapshot exceeds the negotiated frame limit",
            )
            return
        connection.sendall(snapshot_frame)
        while not runtime.stop.is_set():
            if subscription.overflowed():
                _send_protocol_error(
                    connection,
                    "slow_subscriber",
                    "subscriber queue overflowed; reconnect for a fresh snapshot",
                )
                return

            update = subscription.next_update(SUBSCRIBER_POLL_SECONDS)
            if update is not None:
                try:
                    connection.sendall(encode_frame(update.to_dict()))
                except ProtocolError:
                    _send_protocol_error(
                        connection,
                        "state_update_too_large",
                        "state update exceeds the negotiated frame limit",
                    )
                    return
                except TimeoutError:
                    return
                continue

            readable, _, _ = select.select([connection], [], [], 0)
            if readable:
                data = connection.recv(1, socket.MSG_PEEK)
                if not data:
                    return
                _send_protocol_error(
                    connection,
                    "subscriber_read_only",
                    "subscriber connections cannot send frames",
                )
                return
    finally:
        unsubscribe()


def _handle_interaction_subscriber(
    connection: socket.socket, runtime: _BridgeRuntime
) -> None:
    subscription = InteractionSubscriberQueue(
        stream_id=runtime.stream_id,
        max_queue_frames=runtime.subscriber_queue_frames,
    )
    unsubscribe = runtime.interaction_hub.subscribe(subscription.enqueue)
    connection.settimeout(SUBSCRIBER_WRITE_TIMEOUT_SECONDS)
    try:
        connection.sendall(encode_frame(subscription.started().to_dict()))
        while not runtime.stop.is_set():
            if subscription.overflowed():
                _send_protocol_error(
                    connection,
                    "slow_subscriber",
                    "subscriber queue overflowed; reconnect for a new live stream",
                )
                return

            update = subscription.next_update(SUBSCRIBER_POLL_SECONDS)
            if update is not None:
                try:
                    connection.sendall(encode_frame(update.to_dict()))
                except ProtocolError:
                    _send_protocol_error(
                        connection,
                        "interaction_update_too_large",
                        "interaction update exceeds the negotiated frame limit",
                    )
                    return
                except TimeoutError:
                    return
                continue

            readable, _, _ = select.select([connection], [], [], 0)
            if readable:
                data = connection.recv(1, socket.MSG_PEEK)
                if not data:
                    return
                _send_protocol_error(
                    connection,
                    "subscriber_read_only",
                    "subscriber connections cannot send frames",
                )
                return
    finally:
        unsubscribe()


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
