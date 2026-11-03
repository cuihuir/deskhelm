"""Small protocol v2 peers for tests."""

from __future__ import annotations

from pathlib import Path
from queue import Queue
import select
import socket
import time
from typing import Any

from deskhelm_host import SessionState
from deskhelm_protocol import (
    ApprovalOverflow,
    ApprovalSupport,
    CapabilityMatrix,
    EffectKind,
    EffectOutcome,
    FrameDecoder,
    InputSupport,
    OpenRequest,
    PromptWhileBusy,
    RuntimeIdentity,
    SessionKey,
    encode_frame,
)


class V2Peer:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.decoder = FrameDecoder()
        self.pending: list[dict[str, Any]] = []

    def send(self, frame: dict[str, Any]) -> None:
        self.sock.sendall(encode_frame(frame))

    def next(self, kind: str | None = None, timeout: float = 3.0, where=None) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for index, frame in enumerate(self.pending):
                if (kind is None or frame["type"] == kind) and (where is None or where(frame)):
                    return self.pending.pop(index)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no {kind or 'frame'} arrived")
            readable, _, _ = select.select([self.sock], [], [], remaining)
            if not readable:
                continue
            data = self.sock.recv(65536)
            if not data:
                raise AssertionError(f"connection closed while waiting for {kind}")
            self.pending.extend(self.decoder.feed(data))

    def close(self) -> None:
        self.sock.close()


def operator(path: Path, client_id: str = "web", streams: tuple[str, ...] = ("console",)) -> V2Peer:
    peer = V2Peer(path)
    peer.send({"type": "hello", "protocol_version": 2, "role": "client", "client_id": client_id, "tier": "operator"})
    peer.next("hello_ok")
    for stream in streams:
        peer.send({"type": "subscribe", "stream": stream})
    return peer


def command(
    peer: V2Peer,
    kind: str,
    session: SessionKey,
    payload: dict[str, Any] | None = None,
    *,
    command_id: str = "command-1",
    client_id: str = "web",
) -> dict[str, Any]:
    now = int(time.time() * 1000)
    peer.send(
        {
            "type": "command",
            "command": {
                "command_id": command_id,
                "kind": kind,
                **session.to_dict(),
                "issued_by": client_id,
                "issued_at": now,
                "expires_at": now + 60_000,
                "idempotency_key": command_id,
                "payload": payload or {},
            },
        }
    )
    return peer.next("command_result", where=lambda frame: frame["command_id"] == command_id)


def session_view(snapshot_or_delta: dict[str, Any], key: SessionKey) -> dict[str, Any] | None:
    if snapshot_or_delta["type"] == "snapshot":
        sessions = snapshot_or_delta["state"]["sessions"]
    else:
        sessions = [change["session"] for change in snapshot_or_delta["changes"] if change["op"] == "session"]
    return next(
        (view for view in sessions if SessionKey.from_dict(view) == key),
        None,
    )


class FakeDriver:
    """A session host driver whose vendor is the test."""

    runtime = RuntimeIdentity("deskhelm-fake", "1", "fake", "1")
    capabilities = CapabilityMatrix(
        version=1,
        prompt_while_busy=PromptWhileBusy.QUEUE,
        interrupt=True,
        approvals=ApprovalSupport.ANSWERABLE,
        approval_overflow=ApprovalOverflow.DECLINE,
        user_input=InputSupport.NATIVE_UI,
        resume=False,
    )

    def __init__(self) -> None:
        self.performed: Queue[tuple[EffectKind, dict]] = Queue()
        self.outcome = EffectOutcome.ACCEPTED
        self.fail = False
        self.open_requests: tuple[OpenRequest, ...] = ()
        self.turn_ref: str | None = None

    def start(self, emit) -> None:
        self.emit = emit

    def perform(self, kind: EffectKind, payload: dict) -> EffectOutcome:
        self.performed.put((kind, payload))
        if self.fail:
            raise RuntimeError("vendor state unknown")
        return self.outcome

    def state(self) -> SessionState:
        return SessionState(ready=True, turn_ref=self.turn_ref, open_requests=self.open_requests)

    def wait(self) -> int:
        return 0

    def close(self) -> None:
        return None
