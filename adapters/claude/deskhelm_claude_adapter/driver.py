"""Claude Code stream-json driver for a session host (ADR 0043).

The host owns ``claude -p`` directly: prompts go to its stdin and its stdout
becomes facts. Tool arguments, working directories, and vendor error text
never leave this module; tool names are reduced to bare labels.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
import re
from threading import Lock, Thread
import time
from typing import Any, Callable
import uuid

from deskhelm_host.driver import FactSink, SessionState
from deskhelm_host.vendor import VendorClosed, VendorProcess
from deskhelm_protocol.model import MAX_DETAIL_CHARS
from deskhelm_protocol.model import MAX_TEXT_CHARS as MAX_REPLY_CHARS
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

from .command import claude_argv

MAX_TEXT_CHARS = 8192
MAX_TITLE_CHARS = 2048
MAX_PENDING_APPROVALS = 8
MAX_DECLINED_TOOLS = 64
APPROVAL_WINDOW_MS = 10 * 60 * 1000
_LABEL = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
# Claude asks the user through this tool. It is input, not an approval, and
# this session declares no input support.
_QUESTION_TOOL = "AskUserQuestion"
# The input field that says what a tool works on: a command, a path, a
# pattern, an address, or a task.
_TITLE_FIELDS = ("command", "file_path", "notebook_path", "pattern", "path", "url", "query", "description")


def _label(value: object) -> str:
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else "tool"


def _tool_title(tool_input: object, limit: int = MAX_TITLE_CHARS) -> str:
    if isinstance(tool_input, dict):
        for field in _TITLE_FIELDS:
            value = tool_input.get(field)
            if isinstance(value, str) and value.strip():
                return _clip(value.strip(), limit)
    return ""


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_result_text(value: object) -> str:
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, list):
        parts = [
            item if isinstance(item, str) else item.get("text", "")
            for item in value
            if isinstance(item, str) or (isinstance(item, dict) and isinstance(item.get("text"), str))
        ]
        return _clip("".join(parts))
    return ""


class ClaudeSession:
    capabilities = CapabilityMatrix(
        version=1,
        prompt_while_busy=PromptWhileBusy.QUEUE,
        interrupt=True,
        approvals=ApprovalSupport.ANSWERABLE,
        approval_overflow=ApprovalOverflow.DECLINE,
        user_input=InputSupport.NONE,
        resume=False,
        catalog=frozenset({"models", "mcp", "skills"}),
    )

    def __init__(
        self,
        cwd: Path,
        *,
        runtime_version: str = "claude-code",
        adapter_version: str = "0.3.0",
        vendor: VendorProcess | None = None,
        clock: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self.runtime = RuntimeIdentity("deskhelm-claude", adapter_version, "claude-code", runtime_version)
        self._vendor = vendor or VendorProcess(claude_argv(), cwd)
        self._clock = clock
        self._lock = Lock()
        self._emit: FactSink = lambda kind, meta=None, content=None: None
        self._ready = False
        self._turn_ref: str | None = None
        self._interrupting = False
        self._echoes: deque[str] = deque(maxlen=8)
        self._pending: dict[str, OpenRequest] = {}
        self._pending_input: dict[str, dict[str, Any]] = {}
        # The tool each permission request is about, and the tools refused,
        # so a refused tool's error result reads as declined, not failed.
        self._pending_tools: dict[str, str] = {}
        self._declined: dict[str, None] = {}
        self._tools: dict[str, str] = {}
        self._assistant_ref = ""
        self._assistant_seen = False
        self._delta = ""
        # What streamed of the current reply, for a reply that is cut off.
        self._streamed = ""
        self._catalog: dict[str, Any] | None = None
        self._initialize_ref = uuid.uuid4().hex
        self._reader: Thread | None = None

    # DriverSession -----------------------------------------------------

    def start(self, emit: FactSink) -> None:
        self._emit = emit
        self._vendor.start()
        self._reader = Thread(target=self._read, name="deskhelm-claude-read", daemon=True)
        self._reader.start()
        # Claude Code 2.1.312 sends system/init only with the first prompt but
        # answers the SDK's initialize request at once, so a new session is
        # ready before anyone writes to it.
        try:
            self._vendor.write_line(
                {"type": "control_request", "request_id": self._initialize_ref, "request": {"subtype": "initialize"}}
            )
        except VendorClosed:
            pass

    def perform(self, kind: EffectKind, payload: dict[str, Any]) -> EffectOutcome:
        try:
            if kind is EffectKind.SUBMIT:
                return self._submit(payload["command_id"], payload["text"])
            if kind is EffectKind.INTERRUPT:
                self._vendor.write_line(
                    {"type": "control_request", "request_id": uuid.uuid4().hex, "request": {"subtype": "interrupt"}}
                )
                with self._lock:
                    self._interrupting = self._turn_ref is not None
                return EffectOutcome.ACCEPTED
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
                catalog=self._catalog,
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
            if not self._vendor.alive():
                return EffectOutcome.UNDELIVERED
            self._echoes.append(text)
            starts_turn = self._turn_ref is None
            if starts_turn:
                self._turn_ref = command_id
                self._assistant_ref, self._assistant_seen, self._delta, self._streamed = "", False, "", ""
            self._emit(
                FactKind.MESSAGE,
                {"message_ref": command_id, "role": "user", "phase": "complete", "command_id": command_id},
                {"text": _clip(text)},
            )
            if starts_turn:
                self._emit(FactKind.TURN_STARTED, {"turn_ref": command_id, "command_id": command_id})
        try:
            self._vendor.write_line(
                {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
            )
        except VendorClosed:
            with self._lock:
                if starts_turn and self._turn_ref == command_id:
                    self._finish_turn({"status": "failed", "failure": Failure.TRANSPORT_LOST.value, "reusable": False})
            raise
        return EffectOutcome.ACCEPTED

    def _respond(self, provider_ref: str, allow: bool) -> EffectOutcome:
        with self._lock:
            request = self._pending.get(provider_ref)
            tool_input = self._pending_input.get(provider_ref, {})
            if request is not None and not allow:
                # Before the write, so the tool's result cannot arrive first.
                self._decline_tool(self._pending_tools.get(provider_ref, ""))
        if request is None:
            return EffectOutcome.UNDELIVERED
        self._write_decision(provider_ref, allow, tool_input, "denied")
        with self._lock:
            self._pending.pop(provider_ref, None)
            self._pending_input.pop(provider_ref, None)
            self._pending_tools.pop(provider_ref, None)
        return EffectOutcome.ACCEPTED

    def _write_decision(self, provider_ref: str, allow: bool, tool_input: dict[str, Any], reason: str) -> None:
        response: dict[str, Any] = {"behavior": "allow" if allow else "deny"}
        if allow:
            response["updatedInput"] = tool_input
        else:
            response["message"] = reason
        self._vendor.write_line(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": provider_ref, "response": response},
            }
        )

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
        kind = value.get("type")
        if kind == "system" and value.get("subtype") == "init":
            self._init(value)
        elif kind == "stream_event":
            event = value.get("event")
            delta = event.get("delta") if isinstance(event, dict) else None
            if isinstance(delta, dict) and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                self._delta += delta["text"]
                self._streamed = (self._streamed + delta["text"])[:MAX_REPLY_CHARS]
                self._assistant_seen = True
                self._assistant_ref = self._assistant_ref or uuid.uuid4().hex
        elif kind == "assistant":
            self._assistant(value)
        elif kind == "user":
            self._user(value)
        elif kind == "result":
            self._result(value)
        elif kind == "control_request":
            self._control_request(value)
        elif kind == "control_response":
            # The answer to initialize names the account, commands, and
            # agents; only its arrival is used.
            response = value.get("response")
            if isinstance(response, dict) and response.get("request_id") == self._initialize_ref:
                self._become_ready()

    def _become_ready(self) -> None:
        if not self._ready:
            self._ready = True
            self._emit(FactKind.READY)

    def _init(self, value: dict[str, Any]) -> None:
        model = value.get("model") if isinstance(value.get("model"), str) else ""
        raw_skills = value.get("skills") if isinstance(value.get("skills"), list) else []
        raw_servers = value.get("mcp_servers") if isinstance(value.get("mcp_servers"), list) else []
        names = (
            _label(item if isinstance(item, str) else item.get("name"))
            for item in raw_skills
            if isinstance(item, (str, dict))
        )
        skills = [
            {"name": name, "description": "", "scope": "unknown", "enabled": True}
            for name in names
            if name != "tool"
        ]
        servers = [
            {"name": _label(item.get("name")), "status": str(item.get("status") or "unknown")[:32], "auth": "", "tools": []}
            for item in raw_servers
            if isinstance(item, dict) and _label(item.get("name")) != "tool"
        ]
        self._catalog = {
            "model": model[:128],
            "models": [{"id": model[:128], "name": model[:128], "is_default": True}] if model else [],
            "mcp_servers": servers[:32],
            "skills": skills[:64],
        }
        self._emit(FactKind.CATALOG, content={"catalog": self._catalog})
        self._become_ready()

    def _assistant(self, value: dict[str, Any]) -> None:
        message = value.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"]:
                self._complete_assistant(block["text"])
            elif block.get("type") == "tool_use":
                self._flush_delta()
                tool_ref = block.get("id") if isinstance(block.get("id"), str) and block["id"] else uuid.uuid4().hex
                label = _label(block.get("name"))
                self._tools[tool_ref] = label
                # The command or path is content, which only the operator sees.
                title = _tool_title(block.get("input"))
                self._emit(
                    FactKind.TOOL, {"tool_ref": tool_ref, "label": label, "phase": "start"}, {"text": title} if title else None
                )

    def _user(self, value: dict[str, Any]) -> None:
        message = value.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content:
            if content in self._echoes:
                self._echoes.remove(content)
                return
            ref = uuid.uuid4().hex
            self._emit(FactKind.MESSAGE, {"message_ref": ref, "role": "user", "phase": "complete"}, {"text": _clip(content)})
            return
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_ref = block.get("tool_use_id") if isinstance(block.get("tool_use_id"), str) and block["tool_use_id"] else "tool"
            label = self._tools.get(tool_ref, "tool")
            text = _tool_result_text(block.get("content"))
            # Claude reports a failed or refused tool as an error result and
            # never an exit code.
            failed = bool(block.get("is_error"))
            if text:
                self._emit(
                    FactKind.TOOL,
                    {"tool_ref": tool_ref, "label": label, "phase": "output", "stream": "stderr" if failed else "stdout"},
                    {"text": text},
                )
            refused = tool_ref in self._declined
            self._declined.pop(tool_ref, None)
            status = "completed" if not failed else "declined" if refused else "failed"
            self._emit(FactKind.TOOL, {"tool_ref": tool_ref, "label": label, "phase": "complete", "status": status})

    def _result(self, value: dict[str, Any]) -> None:
        self._flush_delta()
        failed = value.get("is_error") is True or value.get("subtype") not in {None, "success"}
        result = value.get("result") if isinstance(value.get("result"), str) else ""
        if self._assistant_ref:
            # Cut off before Claude sent the whole reply: complete with what streamed.
            self._emit_assistant("complete", self._streamed)
        if not failed and result and not self._assistant_seen:
            self._complete_assistant(result)
        if self._interrupting:
            meta: dict[str, Any] = {"status": "interrupted"}
        elif failed:
            failure = Failure.USAGE_LIMIT if "usage limit" in result.lower() else Failure.PROVIDER
            meta = {"status": "failed", "failure": failure.value}
        else:
            meta = {"status": "completed"}
        self._finish_turn(meta)

    def _control_request(self, value: dict[str, Any]) -> None:
        request = value.get("request")
        provider_ref = value.get("request_id")
        if not isinstance(provider_ref, str) or not provider_ref or not isinstance(request, dict):
            return
        if request.get("subtype") != "can_use_tool":
            # An unanswered request would stall Claude.
            self._vendor_write(
                {"type": "control_response", "response": {"subtype": "error", "request_id": provider_ref, "error": "unsupported request"}}
            )
            return
        tool_input = request.get("input") if isinstance(request.get("input"), dict) else {}
        tool_use_id = request.get("tool_use_id") if isinstance(request.get("tool_use_id"), str) else ""
        if request.get("tool_name") == _QUESTION_TOOL:
            self._decline_tool(tool_use_id)
            self._vendor_write_decision(provider_ref, tool_input, "The user cannot answer questions here. Continue without asking.")
            return
        if len(self._pending) >= MAX_PENDING_APPROVALS:
            self._decline_tool(tool_use_id)
            self._vendor_write_decision(provider_ref, tool_input, "too many pending approvals")
            return
        description = request.get("description") if isinstance(request.get("description"), str) else ""
        open_request = OpenRequest(
            provider_ref=provider_ref,
            kind=RequestKind.APPROVAL,
            summary=f"请求使用工具 {_label(request.get('tool_name'))}",
            expires_at=self._clock() + APPROVAL_WINDOW_MS,
            answerable=True,
            # What the tool would do, so the operator sees what they allow.
            detail=_tool_title(tool_input, MAX_DETAIL_CHARS) or _clip(description.strip(), MAX_DETAIL_CHARS),
        )
        self._pending[provider_ref] = open_request
        self._pending_input[provider_ref] = tool_input
        self._pending_tools[provider_ref] = tool_use_id
        self._emit(
            FactKind.REQUEST_OPENED,
            {
                "provider_ref": provider_ref,
                "kind": "approval",
                "summary": open_request.summary,
                "expires_at": open_request.expires_at,
                "answerable": True,
            },
            {"detail": open_request.detail} if open_request.detail else None,
        )

    # Helpers (called with the lock held) -------------------------------

    def _complete_assistant(self, text: str) -> None:
        self._assistant_seen = True
        self._assistant_ref = self._assistant_ref or uuid.uuid4().hex
        self._delta = ""
        self._emit_assistant("complete", text)

    def _flush_delta(self) -> None:
        if self._delta:
            text, self._delta = self._delta, ""
            self._emit_assistant("delta", text)

    def _emit_assistant(self, phase: str, text: str) -> None:
        # A complete reply replaces what streamed, so it keeps up to the
        # protocol's limit; deltas stay small.
        self._emit(
            FactKind.MESSAGE,
            {"message_ref": self._assistant_ref, "role": "assistant", "phase": phase},
            {"text": text[:MAX_REPLY_CHARS] if phase == "complete" else _clip(text)},
        )
        if phase == "complete":
            self._assistant_ref, self._streamed = "", ""

    def _finish_turn(self, meta: dict[str, Any]) -> None:
        for provider_ref in list(self._pending):
            self._settle(provider_ref, "cancelled")
        if self._turn_ref is not None:
            meta = {**meta, "turn_ref": self._turn_ref}
        self._emit(FactKind.TURN_TERMINAL, meta)
        self._turn_ref, self._interrupting = None, False
        self._assistant_ref, self._assistant_seen = "", False

    def _expire(self) -> None:
        now = self._clock()
        for provider_ref, request in list(self._pending.items()):
            if request.expires_at <= now:
                self._decline_tool(self._pending_tools.get(provider_ref, ""))
                self._vendor_write_decision(provider_ref, {}, "approval expired")
                self._settle(provider_ref, "expired")

    def _settle(self, provider_ref: str, by: str) -> None:
        self._pending.pop(provider_ref, None)
        self._pending_input.pop(provider_ref, None)
        self._pending_tools.pop(provider_ref, None)
        self._emit(FactKind.REQUEST_RESOLVED, {"provider_ref": provider_ref, "by": by})

    def _decline_tool(self, tool_use_id: str) -> None:
        if tool_use_id:
            self._declined[tool_use_id] = None
            while len(self._declined) > MAX_DECLINED_TOOLS:
                self._declined.pop(next(iter(self._declined)))

    def _ended(self) -> None:
        self._flush_delta()
        if self._turn_ref is not None:
            self._finish_turn({"status": "failed", "failure": Failure.PROCESS_EXIT.value, "reusable": False})
        self._emit(FactKind.SESSION_ENDED, {"reason": "vendor exited"})

    def _vendor_write(self, value: dict[str, Any]) -> None:
        try:
            self._vendor.write_line(value)
        except VendorClosed:
            pass

    def _vendor_write_decision(self, provider_ref: str, tool_input: dict[str, Any], reason: str) -> None:
        try:
            self._write_decision(provider_ref, False, tool_input, reason)
        except VendorClosed:
            pass
