"""A demo session host without a vendor: ``deskhelm simulate`` (ADR 0043 D4).

It opens one session on the Bridge. Each prompt runs a short scripted turn: a
tool call, then a reply that quotes the prompt. A prompt that contains
"approve" or "批准" first asks for an approval and waits for the decision.
Use it to try the page, the dashboard, and voice without an agent.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sys
from threading import Event, Lock, Thread
import time
from typing import Any, Callable

from deskhelm_protocol import (
    ApprovalOverflow,
    ApprovalSupport,
    CapabilityMatrix,
    EffectKind,
    EffectOutcome,
    FactKind,
    InputSupport,
    OpenRequest,
    PromptWhileBusy,
    RequestKind,
    RuntimeIdentity,
    SessionKey,
)
from deskhelm_protocol.paths import default_socket_path

from .bridge_process import bridge_protocol, ensure_bridge
from .driver import FactSink, SessionState
from .link import BridgeLink

APPROVAL_WINDOW_MS = 5 * 60 * 1000
APPROVAL_WORDS = ("approve", "批准")
DEMO_COMMAND = "echo demo"
_OPEN_TIMEOUT_SECONDS = 10.0


class _Interrupted(Exception):
    pass


class DemoSession:
    runtime = RuntimeIdentity("deskhelm-simulate", "0.3.0", "simulate", "1")
    capabilities = CapabilityMatrix(
        version=1,
        prompt_while_busy=PromptWhileBusy.REJECT,
        interrupt=True,
        approvals=ApprovalSupport.ANSWERABLE,
        approval_overflow=ApprovalOverflow.DECLINE,
        user_input=InputSupport.NONE,
        resume=False,
    )

    def __init__(self, *, step_seconds: float = 0.4, clock: Callable[[], int] = lambda: int(time.time() * 1000)) -> None:
        self.step_seconds = step_seconds
        self._clock = clock
        self._lock = Lock()
        self._emit: FactSink = lambda kind, meta=None, content=None: None
        self._closed = Event()
        self._turn: str | None = None
        self._interrupt = Event()
        self._request: OpenRequest | None = None
        self._decision: bool | None = None
        self._decided = Event()
        self._turns = 0

    # DriverSession -----------------------------------------------------

    def start(self, emit: FactSink) -> None:
        self._emit = emit
        self._emit(FactKind.READY)

    def perform(self, kind: EffectKind, payload: dict[str, Any]) -> EffectOutcome:
        with self._lock:
            if self._closed.is_set():
                return EffectOutcome.UNDELIVERED
            if kind is EffectKind.SUBMIT:
                if self._turn is not None:
                    return EffectOutcome.UNSUPPORTED
                self._turn = payload["command_id"]
                self._turns += 1
                self._interrupt.clear()
                Thread(target=self._run, args=(payload["command_id"], payload["text"], self._turns), daemon=True).start()
                return EffectOutcome.ACCEPTED
            if kind is EffectKind.INTERRUPT:
                if self._turn is None:
                    return EffectOutcome.UNDELIVERED
                self._interrupt.set()
                self._decided.set()
                return EffectOutcome.ACCEPTED
            if kind is EffectKind.RESPOND:
                if self._request is None or self._request.provider_ref != payload["provider_ref"]:
                    return EffectOutcome.UNDELIVERED
                self._decision = payload["decision"] == "approve"
                self._request = None
                self._decided.set()
                return EffectOutcome.ACCEPTED
            return EffectOutcome.UNSUPPORTED

    def state(self) -> SessionState:
        with self._lock:
            return SessionState(
                ready=True,
                turn_ref=self._turn,
                open_requests=() if self._request is None else (self._request,),
            )

    def wait(self) -> int:
        self._closed.wait()
        return 0

    def close(self) -> None:
        self._closed.set()
        self._interrupt.set()
        self._decided.set()

    # The scripted turn -------------------------------------------------

    def _run(self, command_id: str, text: str, number: int) -> None:
        tool_ref = f"demo-tool-{number}"
        try:
            self._emit(FactKind.MESSAGE, {"message_ref": command_id, "role": "user", "phase": "complete", "command_id": command_id}, {"text": text})
            self._emit(FactKind.TURN_STARTED, {"turn_ref": command_id, "command_id": command_id})
            self._step()
            self._emit(FactKind.TOOL, {"tool_ref": tool_ref, "label": "command", "phase": "start"}, {"text": DEMO_COMMAND})
            self._step()
            if any(word in text.lower() for word in APPROVAL_WORDS) and not self._approved(number):
                self._emit(FactKind.TOOL, {"tool_ref": tool_ref, "label": "command", "phase": "complete", "status": "declined"})
                self._reply(f"reply-{number}", "这一步被拒绝了，没有执行。")
            else:
                self._emit(FactKind.TOOL, {"tool_ref": tool_ref, "label": "command", "phase": "output", "stream": "stdout"}, {"text": "demo"})
                self._emit(FactKind.TOOL, {"tool_ref": tool_ref, "label": "command", "phase": "complete",
                                           "exit_code": 0, "status": "completed"})
                self._step()
                self._reply(f"reply-{number}", f"这是模拟会话的回复。你说：{text}")
            meta: dict[str, Any] = {"status": "completed", "turn_ref": command_id}
        except _Interrupted:
            meta = {"status": "interrupted", "turn_ref": command_id}
        with self._lock:
            if self._request is not None:
                self._emit(FactKind.REQUEST_RESOLVED, {"provider_ref": self._request.provider_ref, "by": "cancelled"})
                self._request = None
            self._turn = None
            self._emit(FactKind.TURN_TERMINAL, meta)

    def _approved(self, number: int) -> bool:
        request = OpenRequest(f"demo-approval-{number}", RequestKind.APPROVAL, "请求使用工具 command",
                              self._clock() + APPROVAL_WINDOW_MS, True, DEMO_COMMAND)
        with self._lock:
            # An interrupt sets both events under this lock, so none is lost.
            if self._interrupt.is_set():
                raise _Interrupted()
            self._request, self._decision = request, None
            self._decided.clear()
        self._emit(FactKind.REQUEST_OPENED, {"provider_ref": request.provider_ref, "kind": "approval",
                                             "summary": request.summary, "expires_at": request.expires_at,
                                             "answerable": True}, {"detail": request.detail})
        decided = self._decided.wait(APPROVAL_WINDOW_MS / 1000)
        if self._interrupt.is_set():
            raise _Interrupted()
        if not decided:
            # An unanswered approval expires as a refusal.
            with self._lock:
                self._request = None
            self._emit(FactKind.REQUEST_RESOLVED, {"provider_ref": request.provider_ref, "by": "expired"})
            return False
        return bool(self._decision)

    def _reply(self, ref: str, text: str) -> None:
        half = len(text) // 2
        self._emit(FactKind.MESSAGE, {"message_ref": ref, "role": "assistant", "phase": "delta"}, {"text": text[:half]})
        self._step()
        self._emit(FactKind.MESSAGE, {"message_ref": ref, "role": "assistant", "phase": "complete"}, {"text": text})

    def _step(self) -> None:
        if self._interrupt.wait(self.step_seconds):
            raise _Interrupted()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deskhelm simulate", description="Open a demo session without an agent.")
    parser.add_argument("--socket", type=Path, default=None)
    parser.add_argument("--session-id", default="demo")
    parser.add_argument("--project-id", default="simulate")
    args = parser.parse_args(argv)
    socket_path = args.socket or default_socket_path()
    if bridge_protocol(socket_path) == "other":
        print(f"deskhelm simulate: {socket_path} belongs to something other than a v2 Bridge; stop it first",
              file=sys.stderr)
        return 1
    session = SessionKey("simulate", args.session_id, args.project_id)
    driver = DemoSession()
    link = BridgeLink(socket_path, session, driver, client_id=f"deskhelm-simulate-{args.session_id}",
                      ensure_bridge=ensure_bridge)
    link.start()
    if not link.opened.wait(_OPEN_TIMEOUT_SECONDS):
        print("deskhelm simulate: the Bridge is not reachable yet; continuing", file=sys.stderr)
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda _number, _frame: driver.close())
    driver.start(link.emit)
    print(f"deskhelm simulate: session {session.session_id} on {socket_path}; Ctrl-C stops it", file=sys.stderr)
    try:
        driver.wait()
    finally:
        link.close()
    return 0
