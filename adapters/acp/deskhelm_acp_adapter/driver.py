"""Generic Agent Client Protocol driver for a session host (ADR 0043).

The driver speaks ACP over the vendor's stdio and turns it into facts. Agent
quirks live in a flavor; this module never checks an agent id. Thought text,
tool arguments, raw output, diffs, and paths are not published.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from pathlib import Path
import re
from threading import Lock, Thread
import time
from typing import Any, Callable
import uuid

from deskhelm_host.driver import FactSink, SessionState
from deskhelm_host.vendor import VendorClosed, VendorProcess
from deskhelm_protocol import (
    ApprovalOverflow,
    ApprovalSupport,
    CapabilityMatrix,
    EffectKind,
    EffectOutcome,
    FactKind,
    Failure,
    InputSupport,
    OpenRequest,
    PromptWhileBusy,
    RequestKind,
    RuntimeIdentity,
)

MAX_TEXT_CHARS = 8192
MAX_PENDING_PERMISSIONS = 8
APPROVAL_WINDOW_MS = 10 * 60 * 1000
_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_INITIALIZE_ID = 1
_SESSION_NEW_ID = 2


class AcpFlavor:
    """Agent-specific hooks over the generic ACP driver."""

    adapter_id = "deskhelm-acp"
    runtime_name = "acp"

    def argv(self) -> list[str]:
        raise NotImplementedError

    def capabilities(self, approvals: ApprovalSupport) -> CapabilityMatrix:
        return CapabilityMatrix(
            version=1,
            prompt_while_busy=PromptWhileBusy.REJECT,
            interrupt=True,
            approvals=approvals,
            approval_overflow=ApprovalOverflow.DECLINE,
            user_input=InputSupport.NONE,
            resume=False,
            catalog=frozenset({"models"}),
        )

    def approvals_from_extension(
        self, method: str, params: dict[str, Any], vendor_session: str
    ) -> ApprovalSupport | None:
        """Return the approval support an extension notification reveals."""
        return None


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT_CHARS else text[: MAX_TEXT_CHARS - 1] + "…"


def tool_label(tool: object) -> str:
    """A bare tool name, else the ACP kind; titles can embed commands and paths."""
    if not isinstance(tool, dict):
        return ""
    for key in ("title", "kind"):
        value = tool.get(key)
        if isinstance(value, str) and _LABEL.fullmatch(value):
            return value
    return ""


def content_text(value: object) -> str:
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            return _clip(value["text"])
        # ACP wraps tool output as {"type": "content", "content": {...}}; other
        # blocks, such as diffs with file paths, stay unpublished.
        if value.get("type") == "content":
            return content_text(value.get("content"))
        return ""
    if isinstance(value, list):
        return _clip("".join(content_text(item) for item in value))
    return ""


def select_option(options: object, allow: bool) -> str:
    """The one-time option of the wanted kind; never an "always" option."""
    wanted = "allow_once" if allow else "reject_once"
    for option in options if isinstance(options, list) else []:
        if isinstance(option, dict) and option.get("kind") == wanted:
            option_id = option.get("optionId")
            if isinstance(option_id, str) and option_id.strip():
                return option_id
    return ""


def _flatten(value: object, prefix: str, names: list[str]) -> None:
    if len(names) >= 32:
        return
    if isinstance(value, bool):
        if value and prefix and prefix not in names:
            names.append(prefix[:128])
        return
    if not isinstance(value, dict):
        return
    if prefix and not value and prefix not in names:
        names.append(prefix[:128])
        return
    for key, child in value.items():
        if isinstance(key, str) and key not in {"auth", "authMethods"} and not key.startswith("_"):
            _flatten(child, key if not prefix else f"{prefix}.{key}", names)


class AcpSession:
    def __init__(
        self,
        flavor: AcpFlavor,
        cwd: Path,
        *,
        runtime_version: str,
        adapter_version: str = "0.1.0",
        vendor: VendorProcess | None = None,
        clock: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self.flavor = flavor
        self.runtime = RuntimeIdentity(flavor.adapter_id, adapter_version, flavor.runtime_name, runtime_version)
        self.capabilities = flavor.capabilities(ApprovalSupport.ANSWERABLE)
        self._cwd = cwd
        self._vendor = vendor or VendorProcess(flavor.argv(), cwd)
        self._clock = clock
        self._lock = Lock()
        self._emit: FactSink = lambda kind, meta=None, content=None: None
        self._vendor_session = ""
        self._ready = False
        self._next_id = 10
        self._prompt_id: int | None = None
        self._turn_ref: str | None = None
        self._interrupting = False
        self._echoes: deque[str] = deque(maxlen=8)
        self._pending: dict[str, OpenRequest] = {}
        self._pending_rpc: dict[str, tuple[Any, list[Any]]] = {}
        self._tools: dict[str, str] = {}
        self._tool_output: dict[str, str] = {}
        self._tool_done: set[str] = set()
        self._catalog: dict[str, Any] = {"model": "", "models": [], "agent_capabilities": []}
        self._assistant_ref = ""
        self._delta = ""
        self._reader: Thread | None = None

    # DriverSession -----------------------------------------------------

    def start(self, emit: FactSink) -> None:
        self._emit = emit
        self._vendor.start()
        self._reader = Thread(target=self._read, name="deskhelm-acp-read", daemon=True)
        self._reader.start()
        self._write(
            {
                "jsonrpc": "2.0",
                "id": _INITIALIZE_ID,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                    "clientInfo": {"name": "deskhelm", "version": self.runtime.adapter_version},
                },
            }
        )
        self._write(
            {
                "jsonrpc": "2.0",
                "id": _SESSION_NEW_ID,
                "method": "session/new",
                "params": {"cwd": str(self._cwd), "mcpServers": []},
            }
        )

    def perform(self, kind: EffectKind, payload: dict[str, Any]) -> EffectOutcome:
        try:
            if kind is EffectKind.SUBMIT:
                return self._submit(payload["command_id"], payload["text"])
            if kind is EffectKind.INTERRUPT:
                return self._interrupt()
            if kind is EffectKind.RESPOND:
                return self._respond(payload["provider_ref"], payload["decision"] == "approve")
            return EffectOutcome.UNSUPPORTED
        except VendorClosed:
            return EffectOutcome.UNDELIVERED

    def state(self) -> SessionState:
        with self._lock:
            return SessionState(
                ready=self._ready,
                turn_ref=self._turn_ref,
                open_requests=tuple(self._pending.values()),
                catalog=dict(self._catalog) if self._ready else None,
            )

    def wait(self) -> int:
        code = self._vendor.wait()
        if self._reader is not None:
            self._reader.join(timeout=5)
        return code

    def close(self) -> None:
        self._vendor.terminate()

    # Effects -----------------------------------------------------------

    def _submit(self, command_id: str, text: str) -> EffectOutcome:
        # Record the turn before writing, so a fast reply finds it. The write
        # happens without the lock because a stalled vendor would block it.
        with self._lock:
            if not self._ready:
                return EffectOutcome.UNDELIVERED
            if self._turn_ref is not None:
                return EffectOutcome.UNSUPPORTED
            prompt_id = self._next_id
            self._next_id += 1
            self._prompt_id, self._turn_ref = prompt_id, command_id
            self._echoes.append(text)
            self._assistant_ref, self._delta = "", ""
            self._emit(
                FactKind.MESSAGE,
                {"message_ref": command_id, "role": "user", "phase": "complete", "command_id": command_id},
                {"text": _clip(text)},
            )
            self._emit(FactKind.TURN_STARTED, {"turn_ref": command_id, "command_id": command_id})
        try:
            self._vendor.write_line(
                {
                    "jsonrpc": "2.0",
                    "id": prompt_id,
                    "method": "session/prompt",
                    "params": {"sessionId": self._vendor_session, "prompt": [{"type": "text", "text": text}]},
                }
            )
        except VendorClosed:
            with self._lock:
                if self._prompt_id == prompt_id:
                    self._finish_turn({"status": "failed", "failure": Failure.TRANSPORT_LOST.value, "reusable": False})
            raise
        return EffectOutcome.ACCEPTED

    def _interrupt(self) -> EffectOutcome:
        with self._lock:
            if not self._ready:
                return EffectOutcome.UNDELIVERED
        # ACP cancels with a notification and requires every open permission
        # request to be answered as cancelled.
        self._vendor.write_line(
            {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self._vendor_session}}
        )
        with self._lock:
            self._interrupting = self._turn_ref is not None
            for provider_ref in list(self._pending):
                self._answer_permission(provider_ref, {"outcome": "cancelled"})
                self._settle(provider_ref, "cancelled")
        return EffectOutcome.ACCEPTED

    def _respond(self, provider_ref: str, allow: bool) -> EffectOutcome:
        with self._lock:
            pending = self._pending_rpc.get(provider_ref)
            if pending is None:
                return EffectOutcome.UNDELIVERED
            option_id = select_option(pending[1], allow)
            if not option_id:
                return EffectOutcome.UNSUPPORTED
        self._vendor.write_line(
            {"jsonrpc": "2.0", "id": pending[0], "result": {"outcome": {"outcome": "selected", "optionId": option_id}}}
        )
        with self._lock:
            self._pending.pop(provider_ref, None)
            self._pending_rpc.pop(provider_ref, None)
        return EffectOutcome.ACCEPTED

    # Vendor output -----------------------------------------------------

    def _read(self) -> None:
        try:
            for batch in self._vendor.read_batches():
                with self._lock:
                    for value in batch:
                        self._feed(value)
                    self._flush_delta()
                    self._expire()
        finally:
            with self._lock:
                self._ended()

    def _feed(self, value: dict[str, Any]) -> None:
        method = value.get("method")
        rpc_id = value.get("id")
        if method is None:
            if rpc_id == _INITIALIZE_ID:
                self._initialized(value)
            elif rpc_id == _SESSION_NEW_ID:
                self._session_created(value)
            elif rpc_id is not None and rpc_id == self._prompt_id:
                self._prompt_finished(value)
            return
        if method == "session/update":
            params = value.get("params")
            update = params.get("update") if isinstance(params, dict) else None
            if isinstance(update, dict):
                self._update(update)
        elif method == "session/request_permission" and rpc_id is not None:
            self._permission(rpc_id, value.get("params"))
        elif isinstance(method, str) and method.startswith("_") and rpc_id is None:
            params = value.get("params") if isinstance(value.get("params"), dict) else {}
            support = self.flavor.approvals_from_extension(method, params, self._vendor_session)
            if support is not None and support is not self.capabilities.approvals:
                self.capabilities = replace(self.capabilities, approvals=support)
                self._emit(FactKind.CAPABILITIES, {"capabilities": self.capabilities.to_dict()})
        elif rpc_id is not None:
            # An unanswered agent request would stall the agent.
            self._vendor_write({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Method not found"}})

    def _initialized(self, value: dict[str, Any]) -> None:
        result = value.get("result")
        if isinstance(result, dict):
            names: list[str] = []
            _flatten(result.get("agentCapabilities"), "", names)
            self._catalog["agent_capabilities"] = names

    def _session_created(self, value: dict[str, Any]) -> None:
        result = value.get("result")
        session = result.get("sessionId") if isinstance(result, dict) else None
        if not isinstance(session, str) or not session.strip():
            self._vendor.terminate()
            return
        self._vendor_session = session
        models = result.get("models") if isinstance(result.get("models"), dict) else {}
        current = models.get("currentModelId") if isinstance(models.get("currentModelId"), str) else ""
        catalog_models = []
        for item in models.get("availableModels") or []:
            model_id = item.get("modelId") or item.get("id") if isinstance(item, dict) else item
            if isinstance(model_id, str) and model_id.strip():
                name = item.get("name") if isinstance(item, dict) and isinstance(item.get("name"), str) else model_id
                catalog_models.append({"id": model_id[:128], "name": name[:128], "is_default": model_id == current})
        self._catalog.update({"model": current[:128], "models": catalog_models[:64]})
        self._emit(FactKind.CATALOG, content={"catalog": dict(self._catalog)})
        self._ready = True
        self._emit(FactKind.READY)

    def _prompt_finished(self, value: dict[str, Any]) -> None:
        self._flush_delta()
        if self._assistant_ref:
            self._emit_assistant("complete", "")
        result = value.get("result") if isinstance(value.get("result"), dict) else None
        stop = result.get("stopReason") if result else None
        if self._interrupting or stop in {"cancelled", "canceled"}:
            meta: dict[str, Any] = {"status": "interrupted"}
        elif result is None or stop in {"refusal", "error", "failed"}:
            message = str((value.get("error") or {}).get("message", "")) if isinstance(value.get("error"), dict) else ""
            failure = Failure.USAGE_LIMIT if "limit" in message.lower() else Failure.PROVIDER
            meta = {"status": "failed", "failure": failure.value}
        else:
            meta = {"status": "completed"}
        self._finish_turn(meta)

    def _update(self, update: dict[str, Any]) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = content_text(update.get("content"))
            if text:
                self._assistant_ref = self._assistant_ref or uuid.uuid4().hex
                self._delta += text
        elif kind == "user_message_chunk":
            text = content_text(update.get("content"))
            if text in self._echoes:
                self._echoes.remove(text)
            elif text:
                self._emit(FactKind.MESSAGE, {"message_ref": uuid.uuid4().hex, "role": "user", "phase": "complete"}, {"text": text})
        elif kind == "tool_call":
            self._flush_delta()
            tool_ref = update.get("toolCallId") if isinstance(update.get("toolCallId"), str) and update["toolCallId"] else uuid.uuid4().hex
            label = tool_label(update) or "tool"
            self._tools[tool_ref] = label
            self._emit(FactKind.TOOL, {"tool_ref": tool_ref, "label": label, "phase": "start"})
        elif kind == "tool_call_update":
            self._flush_delta()
            self._tool_update(update)

    def _tool_update(self, update: dict[str, Any]) -> None:
        tool_ref = update.get("toolCallId") if isinstance(update.get("toolCallId"), str) and update["toolCallId"] else "tool"
        label = tool_label(update) or self._tools.get(tool_ref, "tool")
        text = content_text(update.get("content"))
        # Vendors repeat the same content on later status updates.
        if text and self._tool_output.get(tool_ref) != text:
            self._tool_output[tool_ref] = text
            self._emit(FactKind.TOOL, {"tool_ref": tool_ref, "label": label, "phase": "output", "stream": "stdout"}, {"text": text})
        status = update.get("status")
        if status in {"completed", "failed"} and tool_ref not in self._tool_done:
            self._tool_done.add(tool_ref)
            exit_code = 0 if status == "completed" else 1
            self._emit(FactKind.TOOL, {"tool_ref": tool_ref, "label": label, "phase": "complete", "exit_code": exit_code})

    def _permission(self, rpc_id: Any, params: object) -> None:
        params = params if isinstance(params, dict) else {}
        options = params.get("options") if isinstance(params.get("options"), list) else []
        provider_ref = f"rpc-{rpc_id}"
        if len(self._pending) >= MAX_PENDING_PERMISSIONS:
            option_id = select_option(options, False)
            outcome = {"outcome": "selected", "optionId": option_id} if option_id else {"outcome": "cancelled"}
            self._vendor_write({"jsonrpc": "2.0", "id": rpc_id, "result": {"outcome": outcome}})
            return
        request = OpenRequest(
            provider_ref=provider_ref,
            kind=RequestKind.APPROVAL,
            summary=f"请求使用工具 {tool_label(params.get('toolCall')) or 'tool'}",
            expires_at=self._clock() + APPROVAL_WINDOW_MS,
            answerable=bool(select_option(options, True) or select_option(options, False)),
        )
        self._pending[provider_ref] = request
        self._pending_rpc[provider_ref] = (rpc_id, options)
        self._emit(
            FactKind.REQUEST_OPENED,
            {
                "provider_ref": provider_ref,
                "kind": "approval",
                "summary": request.summary,
                "expires_at": request.expires_at,
                "answerable": request.answerable,
            },
        )

    # Helpers (called with the lock held) -------------------------------

    def _flush_delta(self) -> None:
        if self._delta:
            text, self._delta = self._delta, ""
            self._emit_assistant("delta", text)

    def _emit_assistant(self, phase: str, text: str) -> None:
        self._emit(FactKind.MESSAGE, {"message_ref": self._assistant_ref, "role": "assistant", "phase": phase}, {"text": _clip(text)})
        if phase == "complete":
            self._assistant_ref = ""

    def _finish_turn(self, meta: dict[str, Any]) -> None:
        for provider_ref in list(self._pending):
            self._answer_permission(provider_ref, {"outcome": "cancelled"})
            self._settle(provider_ref, "cancelled")
        if self._turn_ref is not None:
            meta = {**meta, "turn_ref": self._turn_ref}
        self._emit(FactKind.TURN_TERMINAL, meta)
        self._turn_ref, self._prompt_id, self._interrupting = None, None, False
        self._tool_output.clear()
        self._tool_done.clear()

    def _expire(self) -> None:
        now = self._clock()
        for provider_ref, request in list(self._pending.items()):
            if request.expires_at <= now:
                _, options = self._pending_rpc[provider_ref]
                option_id = select_option(options, False)
                self._answer_permission(provider_ref, {"outcome": "selected", "optionId": option_id} if option_id else {"outcome": "cancelled"})
                self._settle(provider_ref, "expired")

    def _answer_permission(self, provider_ref: str, outcome: dict[str, Any]) -> None:
        pending = self._pending_rpc.get(provider_ref)
        if pending is not None:
            self._vendor_write({"jsonrpc": "2.0", "id": pending[0], "result": {"outcome": outcome}})

    def _settle(self, provider_ref: str, by: str) -> None:
        self._pending.pop(provider_ref, None)
        self._pending_rpc.pop(provider_ref, None)
        self._emit(FactKind.REQUEST_RESOLVED, {"provider_ref": provider_ref, "by": by})

    def _ended(self) -> None:
        self._flush_delta()
        if self._turn_ref is not None:
            self._finish_turn({"status": "failed", "failure": Failure.PROCESS_EXIT.value, "reusable": False})
        self._emit(FactKind.SESSION_ENDED, {"reason": "vendor exited"})

    def _write(self, value: dict[str, Any]) -> None:
        self._vendor.write_line(value)

    def _vendor_write(self, value: dict[str, Any]) -> None:
        try:
            self._vendor.write_line(value)
        except VendorClosed:
            pass
