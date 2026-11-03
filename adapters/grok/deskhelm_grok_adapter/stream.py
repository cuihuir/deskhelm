from __future__ import annotations

from typing import Any
import uuid

from deskhelm_bridge.interaction import (
    ApprovalRequestPayload,
    InteractionEvent,
    InteractionKind,
    MessagePayload,
    MessagePhase,
    MessageRole,
    TaskPayload,
    ToolPayload,
    ToolPhase,
    ToolStream,
)
from deskhelm_bridge.session_registry import SessionKey


_MAX_TEXT = 8192
_APPROVAL_WINDOW_MS = 10 * 60 * 1000


class GrokEventDecoder:
    """Map Grok ACP JSON-RPC onto DeskHelm interaction events.

    Thought text, tool arguments, and filesystem paths are not published.
    Each submitted prompt keeps its own correlation id. A vendor echo of
    that same text is not published again.
    """

    def __init__(self, session: SessionKey, source_version: str) -> None:
        self.session = session
        self.source_version = source_version
        self.sequence = 0
        self.session_id_vendor = ""
        self.catalog: dict[str, Any] = {
            "notice": "",
            "model": "",
            "reasoning_effort": "",
            "models": [],
            "models_notice": "unavailable",
            "mcp_servers": [],
            "mcp_notice": "unavailable",
            "skills": [],
            "skills_notice": "",
            "agent_capabilities": [],
            "truncated": False,
        }
        self.pending_permissions: dict[str, dict[str, Any]] = {}
        self._assistant_id = ""
        self._assistant_open = False
        self._turn_open = False
        self._terminal = False
        self._next_prompt_id = 10
        self._outbound_prompt = ""
        self._tool_names: dict[str, str] = {}

    def next_prompt(self, text: str) -> dict[str, Any]:
        if not self.session_id_vendor:
            raise RuntimeError("grok session is not ready")
        self._turn_open = True
        self._terminal = False
        self._assistant_id = ""
        self._assistant_open = False
        self._tool_names = {}
        request_id = self._next_prompt_id
        self._next_prompt_id += 1
        self._prompt_id = request_id
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "session/prompt",
            "params": {
                "sessionId": self.session_id_vendor,
                "prompt": [{"type": "text", "text": text}],
            },
        }

    def cancel_request(self) -> dict[str, Any]:
        if not self.session_id_vendor:
            raise RuntimeError("grok session is not ready")
        request_id = self._next_prompt_id
        self._next_prompt_id += 1
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "session/cancel",
            "params": {"sessionId": self.session_id_vendor},
        }

    def permission_result(self, request_id: str, *, allow: bool) -> dict[str, Any]:
        pending = self.pending_permissions.pop(request_id, None)
        if pending is None:
            raise RuntimeError("approval request is not pending")
        option_id = _select_option(pending.get("options"), allow=allow)
        if not option_id:
            raise RuntimeError("approval option is unavailable")
        return {
            "jsonrpc": "2.0",
            "id": pending["rpc_id"],
            "result": {"outcome": {"outcome": "selected", "optionId": option_id}},
        }

    def user_message(self, text: str, correlation_id: str) -> InteractionEvent:
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            raise ValueError("user message correlation id is required")
        self._outbound_prompt = text
        return self._message(
            correlation_id,
            MessageRole.USER,
            MessagePhase.COMPLETE,
            text,
        )

    def feed(self, value: dict[str, Any]) -> list[InteractionEvent]:
        if not isinstance(value, dict):
            return []
        if value.get("id") == 1 and isinstance(value.get("result"), dict):
            self._initialize(value["result"])
            return []
        if value.get("id") == 2 and isinstance(value.get("result"), dict):
            self._session(value["result"])
            return []
        if value.get("method") == "session/request_permission":
            return self._permission(value)
        if value.get("method") == "session/update":
            return self._update(value.get("params"))
        if value.get("id") == getattr(self, "_prompt_id", None) and "result" in value:
            return self._prompt_result(value.get("result"))
        if value.get("id") == getattr(self, "_prompt_id", None) and "error" in value:
            return self._fail()
        return []

    def stream_ended(self) -> list[InteractionEvent]:
        if not self._turn_open or self._terminal:
            return []
        return self._fail()

    def _initialize(self, result: dict[str, Any]) -> None:
        capabilities = result.get("agentCapabilities")
        names = _flatten_capabilities(capabilities)
        if names:
            self.catalog["agent_capabilities"] = names

    def _session(self, result: dict[str, Any]) -> None:
        session_id = result.get("sessionId")
        if isinstance(session_id, str) and session_id.strip():
            self.session_id_vendor = session_id.strip()
        models = result.get("models")
        if not isinstance(models, dict):
            return
        current = models.get("currentModelId")
        current_id = current.strip() if isinstance(current, str) else ""
        catalog_models = []
        available = models.get("availableModels")
        if isinstance(available, list):
            for item in available:
                model_id = ""
                name = ""
                if isinstance(item, str):
                    model_id = name = item.strip()
                elif isinstance(item, dict):
                    raw_id = item.get("id") or item.get("modelId") or item.get("model")
                    raw_name = item.get("name") or item.get("displayName") or raw_id
                    model_id = raw_id.strip() if isinstance(raw_id, str) else ""
                    name = raw_name.strip() if isinstance(raw_name, str) else model_id
                if not model_id:
                    continue
                catalog_models.append(
                    {
                        "id": model_id[:128],
                        "name": (name or model_id)[:128],
                        "is_default": model_id == current_id,
                        "default_effort": "",
                        "efforts": [],
                    }
                )
        if current_id and all(item["id"] != current_id for item in catalog_models):
            catalog_models.insert(
                0,
                {
                    "id": current_id[:128],
                    "name": current_id[:128],
                    "is_default": True,
                    "default_effort": "",
                    "efforts": [],
                },
            )
        if catalog_models or current_id:
            self.catalog["model"] = current_id[:128]
            self.catalog["models"] = catalog_models
            self.catalog["models_notice"] = ""

    def _update(self, params: object) -> list[InteractionEvent]:
        if not isinstance(params, dict):
            return []
        update = params.get("update")
        if not isinstance(update, dict):
            return []
        kind = update.get("sessionUpdate")
        if kind in {"agent_thought_chunk", "plan"}:
            return []
        if kind in {"agent_message_chunk", "user_message_chunk"}:
            text = _content_text(update.get("content"))
            if not text:
                return []
            if kind == "user_message_chunk":
                if text == self._outbound_prompt:
                    self._outbound_prompt = ""
                    return []
                return [
                    self._message(
                        uuid.uuid4().hex,
                        MessageRole.USER,
                        MessagePhase.COMPLETE,
                        text,
                    )
                ]
            return [self._assistant_delta(text)]
        if kind == "tool_call":
            tool = update.get("toolCall") if isinstance(update.get("toolCall"), dict) else update
            tool_id = _token(tool.get("toolCallId")) or uuid.uuid4().hex
            name = _token(tool.get("title") or tool.get("kind")) or "tool"
            self._tool_names[tool_id] = name
            return [
                self._event(
                    InteractionKind.TOOL,
                    ToolPayload(tool_call_id=tool_id, name=name, phase=ToolPhase.START),
                    tool_id,
                )
            ]
        if kind == "tool_call_update":
            tool = update.get("toolCall") if isinstance(update.get("toolCall"), dict) else update
            tool_id = _token(tool.get("toolCallId") or update.get("toolCallId")) or "tool"
            text = _content_text(update.get("content") or tool.get("content"))
            name = _token(tool.get("title") or tool.get("kind")) or self._tool_names.get(tool_id, "tool")
            events = []
            if text:
                events.append(
                    self._event(
                        InteractionKind.TOOL,
                        ToolPayload(
                            tool_call_id=tool_id,
                            name=name,
                            phase=ToolPhase.OUTPUT,
                            text=text,
                            stream=ToolStream.STDOUT,
                        ),
                        tool_id,
                    )
                )
            status = tool.get("status") or update.get("status")
            if status in {"completed", "failed", "complete"}:
                events.append(
                    self._event(
                        InteractionKind.TOOL,
                        ToolPayload(
                            tool_call_id=tool_id,
                            name=name,
                            phase=ToolPhase.COMPLETE,
                            exit_code=0 if status != "failed" else 1,
                        ),
                        tool_id,
                    )
                )
            return events
        return []

    def _permission(self, value: dict[str, Any]) -> list[InteractionEvent]:
        params = value.get("params")
        rpc_id = value.get("id")
        if not isinstance(params, dict) or isinstance(rpc_id, bool) or not isinstance(rpc_id, (int, str)):
            return []
        if len(self.pending_permissions) >= 8:
            return []
        request_id = uuid.uuid4().hex
        options = params.get("options") if isinstance(params.get("options"), list) else []
        title = ""
        tool = params.get("toolCall")
        if isinstance(tool, dict):
            title = _token(tool.get("title") or tool.get("kind"))
        self.pending_permissions[request_id] = {"rpc_id": rpc_id, "options": options}
        occurred_at = _now()
        return [
            self._event(
                InteractionKind.APPROVAL_REQUEST,
                ApprovalRequestPayload(
                    request_id=request_id,
                    summary=f"请求使用工具 {title or 'tool'}",
                    expires_at=occurred_at + _APPROVAL_WINDOW_MS,
                ),
                request_id,
                occurred_at=occurred_at,
            )
        ]

    def _prompt_result(self, result: object) -> list[InteractionEvent]:
        self._terminal = True
        self._turn_open = False
        events = [self._assistant_complete()] if self._assistant_open else []
        stop = ""
        if isinstance(result, dict):
            raw = result.get("stopReason")
            stop = raw if isinstance(raw, str) else ""
        if stop in {"cancelled", "canceled", "error", "failed"}:
            events.append(self._task(InteractionKind.TASK_FAILED, "这一轮失败", "agent_failed"))
            return events
        events.append(self._task(InteractionKind.TASK_COMPLETED, "", ""))
        return events

    def _fail(self) -> list[InteractionEvent]:
        self._terminal = True
        self._turn_open = False
        return [self._task(InteractionKind.TASK_FAILED, "这一轮失败", "agent_failed")]

    def _assistant_delta(self, text: str) -> InteractionEvent:
        if not self._assistant_id:
            self._assistant_id = uuid.uuid4().hex
        self._assistant_open = True
        return self._message(
            self._assistant_id,
            MessageRole.ASSISTANT,
            MessagePhase.DELTA,
            text,
        )

    def _assistant_complete(self) -> InteractionEvent:
        correlation = self._assistant_id or uuid.uuid4().hex
        self._assistant_open = False
        return self._message(
            correlation,
            MessageRole.ASSISTANT,
            MessagePhase.COMPLETE,
            "",
        )

    def _message(
        self,
        correlation_id: str,
        role: MessageRole,
        phase: MessagePhase,
        text: str,
    ) -> InteractionEvent:
        return self._event(
            InteractionKind.MESSAGE,
            MessagePayload(role=role, phase=phase, text=_clip(text)),
            correlation_id,
        )

    def _task(self, kind: InteractionKind, message: str, code: str) -> InteractionEvent:
        return self._event(kind, TaskPayload(message=message, error_code=code), "")

    def _event(
        self,
        kind: InteractionKind,
        payload: object,
        correlation_id: str,
        occurred_at: int | None = None,
    ) -> InteractionEvent:
        event = InteractionEvent(
            kind=kind,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            source="grok-stdio",
            source_version=self.source_version,
            sequence=self.sequence,
            payload=payload,  # type: ignore[arg-type]
            correlation_id=correlation_id,
            occurred_at=occurred_at or 0,
        )
        self.sequence += 1
        return event


def _flatten_capabilities(value: object) -> list[str]:
    names: list[str] = []

    def walk(item: object, prefix: str) -> None:
        if len(names) >= 32:
            return
        if isinstance(item, bool):
            if item and prefix and prefix not in names:
                names.append(prefix[:128])
            return
        if not isinstance(item, dict):
            return
        if prefix and not item and prefix not in names:
            names.append(prefix[:128])
            return
        for key, child in item.items():
            if len(names) >= 32:
                return
            if not isinstance(key, str) or key in {"auth", "authMethods"} or key.startswith("_"):
                continue
            walk(child, key if not prefix else f"{prefix}.{key}")

    walk(value, "")
    return names


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, dict) and value.get("type") == "text" and isinstance(value.get("text"), str):
        return _clip(value["text"])
    if isinstance(value, list):
        return _clip("".join(_content_text(item) for item in value))
    return ""


def _select_option(options: object, *, allow: bool) -> str:
    if not isinstance(options, list):
        return ""
    wanted = ("allow", "approve", "accept") if allow else ("reject", "deny", "cancel")
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = option.get("optionId") or option.get("id")
        kind = option.get("kind") or ""
        if not isinstance(option_id, str):
            continue
        haystack = f"{option_id} {kind}".lower()
        if any(word in haystack for word in wanted):
            return option_id
    return ""


def _token(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:128]


def _clip(value: str) -> str:
    if len(value) <= _MAX_TEXT:
        return value
    return value[: _MAX_TEXT - 1] + "…"


def _now() -> int:
    from time import time

    return int(time() * 1000)
