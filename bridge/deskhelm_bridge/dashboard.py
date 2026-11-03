from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import select
import socket
import sys
import termios
import time
import tty
from typing import Any, BinaryIO

from .client import get_session_snapshot, send_control_command
from .control import (
    ApprovalDecisionPayload,
    ControlCommand,
    ControlKind,
    FocusPayload,
    InterruptPayload,
)
from .event import AgentEvent, PROTOCOL_VERSION
from .transport import (
    ClientHello,
    ClientRole,
    CONTROL_COMMAND_V1_CAPABILITY,
    INTERACTION_SUBSCRIPTION_V1_CAPABILITY,
    PROTOCOL_ERROR_MESSAGE_TYPE,
    STATE_SUBSCRIPTION_V1_CAPABILITY,
    decode_json_object,
    encode_frame,
    read_frame,
)


MAX_DETAIL_EVENTS = 8
MAX_DETAIL_CHARS = 160


@dataclass(slots=True)
class _DashboardState:
    events: dict[int, AgentEvent] = field(default_factory=dict)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    focused: tuple[str, str, str] | None = None
    cursor: int = 0
    details: dict[tuple[str, str, str], list[str]] = field(default_factory=dict)
    approvals: dict[tuple[str, str, str], tuple[str, str, int]] = field(default_factory=dict)
    notice: str = ""

    def rows(self) -> list[dict[str, Any]]:
        rows = []
        for record in self.sessions:
            event = self.events.get(int(record.get("slot", -1)))
            rows.append({"record": record, "event": event})
        return rows


def run_dashboard(socket_path: Path, *, client_id: str = "deskhelm-dashboard") -> int:
    """Run the dependency-free local TUI dashboard prototype."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("dashboard requires an interactive terminal")
    state = _DashboardState()
    state_socket, state_reader = _connect_subscriber(
        socket_path, client_id=f"{client_id}-state", capability=STATE_SUBSCRIPTION_V1_CAPABILITY
    )
    interaction_socket, interaction_reader = _connect_subscriber(
        socket_path,
        client_id=f"{client_id}-interaction",
        capability=INTERACTION_SUBSCRIPTION_V1_CAPABILITY,
    )
    original = termios.tcgetattr(sys.stdin.fileno())
    try:
        tty.setcbreak(sys.stdin.fileno())
        _refresh_sessions(socket_path, state, client_id)
        while True:
            _render(state)
            readable, _, _ = select.select(
                [sys.stdin, state_socket, interaction_socket], [], [], 0.75
            )
            if not readable:
                _refresh_sessions(socket_path, state, client_id)
                continue
            for source in readable:
                if source is sys.stdin:
                    key = os.read(sys.stdin.fileno(), 8)
                    if key in {b"q", b"Q", b"\x03"}:
                        return 0
                    _handle_key(key, state, socket_path, client_id)
                elif source is state_socket:
                    _read_state(state_reader, state)
                else:
                    _read_interaction(interaction_reader, state)
            _refresh_sessions(socket_path, state, client_id)
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)
        state_reader.close()
        interaction_reader.close()
        state_socket.close()
        interaction_socket.close()
        sys.stdout.write("\033[0m\033[2J\033[H")
        sys.stdout.flush()


def _connect_subscriber(
    socket_path: Path, *, client_id: str, capability: str
) -> tuple[socket.socket, BinaryIO]:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(socket_path))
        reader = client.makefile("rb")
        hello = ClientHello(
            client_id=client_id,
            role=ClientRole.SUBSCRIBER,
            supported_versions=(PROTOCOL_VERSION,),
            capabilities=(capability,),
        )
        client.sendall(encode_frame(hello.to_dict()))
        response = decode_json_object(read_frame(reader) or b"")
        if response.get("message_type") == PROTOCOL_ERROR_MESSAGE_TYPE:
            raise ConnectionError(response.get("message", "bridge rejected dashboard"))
        if capability not in response.get("accepted_capabilities", ()):
            raise ConnectionError(f"bridge did not accept {capability}")
        return client, reader
    except BaseException:
        client.close()
        raise


def _read_state(reader: BinaryIO, state: _DashboardState) -> None:
    frame = read_frame(reader)
    if frame is None:
        raise ConnectionError("bridge state subscription closed")
    value = decode_json_object(frame)
    if value.get("message_type") == "state_snapshot":
        state.events = {
            int(event["slot"]): AgentEvent.from_dict(event)
            for event in value.get("events", [])
        }
    elif value.get("message_type") == "state_update":
        event = AgentEvent.from_dict(value["event"])
        state.events[event.slot] = event


def _read_interaction(reader: BinaryIO, state: _DashboardState) -> None:
    frame = read_frame(reader)
    if frame is None:
        raise ConnectionError("bridge interaction subscription closed")
    value = decode_json_object(frame)
    if value.get("message_type") != "interaction_update":
        return
    event = value.get("event")
    if not isinstance(event, dict):
        return
    key = (str(event.get("agent_id", "")), str(event.get("session_id", "")), str(event.get("project_id", "")))
    payload = event.get("payload")
    if event.get("kind") == "approval_request" and isinstance(payload, dict):
        request_id = payload.get("request_id")
        summary = payload.get("summary")
        expires_at = payload.get("expires_at")
        if isinstance(request_id, str) and isinstance(summary, str) and isinstance(expires_at, int):
            # Keep the exact summary; the router rejects a decision that changes it.
            state.approvals[key] = (request_id, summary, expires_at)
    text = payload.get("text") if isinstance(payload, dict) else None
    summary = str(text or event.get("kind", "interaction"))[:MAX_DETAIL_CHARS]
    entries = state.details.setdefault(key, [])
    entries.append(f"{event.get('kind', 'event')}: {summary}")
    del entries[:-MAX_DETAIL_EVENTS]


def _refresh_sessions(socket_path: Path, state: _DashboardState, client_id: str) -> None:
    try:
        result = get_session_snapshot(socket_path, client_id=f"{client_id}-query")
    except (ConnectionError, OSError, ValueError) as error:
        state.notice = str(error)[:MAX_DETAIL_CHARS]
        return
    state.sessions = result.get("sessions", [])
    focused = result.get("focused_session")
    state.focused = _session_tuple(focused) if isinstance(focused, dict) else None
    if state.sessions:
        state.cursor = min(state.cursor, len(state.sessions) - 1)
    else:
        state.cursor = 0


def _handle_key(key: bytes, state: _DashboardState, socket_path: Path, client_id: str) -> None:
    rows = state.rows()
    if key in {b"j", b"\x1b[B"} and rows:
        state.cursor = min(state.cursor + 1, len(rows) - 1)
    elif key in {b"k", b"\x1b[A"} and rows:
        state.cursor = max(state.cursor - 1, 0)
    elif key in {b"f", b"F"} and rows:
        _send_selected(state, socket_path, client_id, ControlKind.FOCUS, FocusPayload())
    elif key in {b"i", b"I"} and rows:
        _send_selected(state, socket_path, client_id, ControlKind.INTERRUPT, InterruptPayload("dashboard"))
    elif key in {b"a", b"A"} and rows:
        _send_approval(state, socket_path, client_id, ControlKind.APPROVE)
    elif key in {b"r", b"R"} and rows:
        _send_approval(state, socket_path, client_id, ControlKind.REJECT)
    elif key == b"?":
        state.notice = "j/k move  f focus  i interrupt  a approve  r reject  q quit"


def _send_selected(state: _DashboardState, socket_path: Path, client_id: str, kind: ControlKind, payload: Any) -> None:
    rows = state.rows()
    record = rows[state.cursor]["record"]
    target = _session_tuple(record)
    if not target or not all(target):
        state.notice = "selected row has no complete session identity"
        return
    now = int(time.time() * 1000)
    command = ControlCommand(
        kind=kind,
        agent_id=target[0],
        session_id=target[1],
        project_id=target[2],
        issued_by=client_id,
        issued_at=now,
        expires_at=now + 5_000,
        idempotency_key=f"dashboard:{kind.value}:{target[0]}:{target[1]}:{now}",
        payload=payload,
    )
    try:
        result = send_control_command(command, socket_path, client_id=client_id)
        state.notice = f"{kind.value}: {result.code.value}"
    except (ConnectionError, OSError, ValueError) as error:
        state.notice = str(error)[:MAX_DETAIL_CHARS]


def _send_approval(
    state: _DashboardState,
    socket_path: Path,
    client_id: str,
    kind: ControlKind,
) -> None:
    rows = state.rows()
    record = rows[state.cursor]["record"]
    target = _session_tuple(record)
    approval = state.approvals.get(target or ("", "", ""))
    if not target or not all(target):
        state.notice = "selected row has no complete session identity"
        return
    if approval is None:
        state.notice = "no pending approval for selected session"
        return
    request_id, summary, expires_at = approval
    now = int(time.time() * 1000)
    if now >= expires_at:
        state.approvals.pop(target, None)
        state.notice = "selected approval has expired"
        return
    command = ControlCommand(
        kind=kind,
        agent_id=target[0],
        session_id=target[1],
        project_id=target[2],
        issued_by=client_id,
        issued_at=now,
        expires_at=expires_at,
        idempotency_key=f"dashboard:{kind.value}:{request_id}:{now}",
        payload=ApprovalDecisionPayload(
            request_id=request_id,
            summary=summary,
            request_expires_at=expires_at,
        ),
    )
    try:
        result = send_control_command(command, socket_path, client_id=client_id)
        state.notice = f"{kind.value}: {result.code.value}"
        if result.code.value == "dispatched":
            state.approvals.pop(target, None)
    except (ConnectionError, OSError, ValueError) as error:
        state.notice = str(error)[:MAX_DETAIL_CHARS]


def _session_tuple(value: dict[str, Any]) -> tuple[str, str, str] | None:
    try:
        return (str(value["agent_id"]), str(value["session_id"]), str(value["project_id"]))
    except (KeyError, TypeError):
        return None


def _render(state: _DashboardState) -> None:
    rows = state.rows()
    selected = rows[state.cursor]["record"] if rows else None
    target = _session_tuple(selected) if selected else None
    details = state.details.get(target or ("", "", ""), [])
    lines = ["DeskHelm Dashboard · j/k move · f focus · i interrupt · a/r approval · q quit", ""]
    lines.append("Sessions")
    if not rows:
        lines.append("  (no registered sessions)")
    for index, row in enumerate(rows):
        record = row["record"]
        event = row["event"]
        identity = _session_tuple(record)
        marker = ">" if index == state.cursor else " "
        focus = "*" if identity is not None and identity == state.focused else " "
        status = event.state.value if event is not None else "unknown"
        label = (event.label if event is not None and event.label else record.get("agent_id", "session"))[:24]
        lifecycle = record.get("state", "unknown")
        lines.append(f"{marker}{focus} {label:<24} {status:<16} {lifecycle}")
    lines.extend(["", "Selected"])
    if selected is None:
        lines.append("  none")
    else:
        lines.append(f"  {selected.get('agent_id')} / {selected.get('session_id')} / {selected.get('project_id')}")
        lines.append(f"  slot={selected.get('slot')} state={selected.get('state')}")
        runtime_name = selected.get("runtime_name") or ""
        if runtime_name:
            lines.append(f"  runtime={runtime_name} {selected.get('runtime_version') or ''}".rstrip())
        capabilities = selected.get("capabilities")
        if isinstance(capabilities, list) and capabilities:
            lines.append("  capabilities=" + ",".join(str(item) for item in capabilities))
        approval = state.approvals.get(target or ("", "", ""))
        if approval is not None:
            request_id, summary, expires_at = approval
            remaining = max(0, (expires_at - int(time.time() * 1000)) // 1000)
            lines.append(f"  approval pending ({remaining}s): {summary[:MAX_DETAIL_CHARS]}")
            lines.append(f"  request_id={request_id}  [display only]")
        lines.append("  live detail:")
        lines.extend(f"    {entry}" for entry in details or ["(no interaction events since dashboard start)"])
    if state.notice:
        lines.extend(["", f"Notice: {state.notice}"])
    sys.stdout.write("\033[2J\033[H" + "\n".join(lines) + "\n")
    sys.stdout.flush()
