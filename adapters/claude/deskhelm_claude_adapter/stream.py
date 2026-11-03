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


class ClaudeEventDecoder:
    """Map Claude stream-json objects onto DeskHelm interaction events.

    Tool arguments, working directories, and vendor error text stay out of
    the events. The catalog keeps names and declared capability tokens.
    Each submitted prompt keeps its own correlation id. A vendor echo of
    that same text is not published again.
    """

    def __init__(self, session: SessionKey, source_version: str) -> None:
        self.session = session
        self.source_version = source_version
        self.sequence = 0
        self.catalog: dict[str, Any] = {
            "notice": "",
            "model": "",
            "reasoning_effort": "",
            "models": [],
            "models_notice": "unavailable",
            "mcp_servers": [],
            "mcp_notice": "unavailable",
            "skills": [],
            "skills_notice": "unavailable",
            "agent_capabilities": [],
            "truncated": False,
        }
        self.pending_approvals: dict[str, dict[str, Any]] = {}
        self._assistant_id = ""
        self._assistant_open = False
        self._assistant_seen = False
        self._turn_open = False
        self._terminal = False
        self._ignored_prompt = ""
        self._tool_names: dict[str, str] = {}

    def note_outbound_prompt(self, text: str) -> None:
        self._ignored_prompt = text
        self._turn_open = True
        self._terminal = False
        self._assistant_id = ""
        self._assistant_open = False
        self._assistant_seen = False

    def user_message(self, text: str, correlation_id: str) -> InteractionEvent:
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            raise ValueError("user message correlation id is required")
        self._ignored_prompt = text
        return self._message(
            correlation_id,
            MessageRole.USER,
            MessagePhase.COMPLETE,
            text,
        )

    def feed(self, value: dict[str, Any]) -> list[InteractionEvent]:
        if not isinstance(value, dict):
            return []
        kind = value.get("type")
        if kind == "system":
            return self._system(value)
        if kind == "stream_event":
            return self._stream(value)
        if kind == "assistant":
            return self._assistant(value)
        if kind == "user":
            return self._user(value)
        if kind == "result":
            return self._result(value)
        if kind == "control_request":
            return self._control_request(value)
        return []

    def stream_ended(self) -> list[InteractionEvent]:
        if not self._turn_open or self._terminal:
            return []
        self._terminal = True
        self._turn_open = False
        return [
            self._task(
                InteractionKind.TASK_FAILED,
                "这一轮没有完成",
                "agent_failed",
            )
        ]

    def _system(self, value: dict[str, Any]) -> list[InteractionEvent]:
        if value.get("subtype") != "init":
            return []
        model = _token(value.get("model"))
        if model:
            self.catalog["model"] = model
            self.catalog["models"] = [
                {
                    "id": model,
                    "name": model,
                    "is_default": True,
                    "default_effort": "",
                    "efforts": [],
                }
            ]
            self.catalog["models_notice"] = ""
        skills = []
        raw_skills = value.get("skills")
        if isinstance(raw_skills, list):
            for item in raw_skills:
                name = item if isinstance(item, str) else ""
                if isinstance(item, dict):
                    name = item.get("name") if isinstance(item.get("name"), str) else ""
                name = _token(name)
                if name:
                    skills.append(
                        {
                            "name": name,
                            "description": "",
                            "scope": "unknown",
                            "enabled": True,
                        }
                    )
            self.catalog["skills"] = skills
            self.catalog["skills_notice"] = ""
        servers = []
        raw_servers = value.get("mcp_servers")
        if isinstance(raw_servers, list):
            for item in raw_servers:
                if not isinstance(item, dict):
                    continue
                name = _token(item.get("name"))
                if not name:
                    continue
                status = item.get("status")
                servers.append(
                    {
                        "name": name,
                        "status": status if isinstance(status, str) else "unknown",
                        "auth": "",
                        "tools": [],
                    }
                )
            self.catalog["mcp_servers"] = servers
            self.catalog["mcp_notice"] = ""
        capabilities = []
        raw_capabilities = value.get("capabilities")
        if isinstance(raw_capabilities, list):
            for item in raw_capabilities:
                token = _token(item)
                if token and token not in capabilities:
                    capabilities.append(token)
        self.catalog["agent_capabilities"] = capabilities
        return []

    def _stream(self, value: dict[str, Any]) -> list[InteractionEvent]:
        event = value.get("event")
        if not isinstance(event, dict):
            return []
        delta = event.get("delta")
        if not isinstance(delta, dict) or delta.get("type") != "text_delta":
            return []
        text = delta.get("text")
        if not isinstance(text, str) or not text:
            return []
        return [self._assistant_delta(text)]

    def _assistant(self, value: dict[str, Any]) -> list[InteractionEvent]:
        message = value.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return []
        events: list[InteractionEvent] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and isinstance(block.get("text"), str) and block["text"]:
                events.extend(self._assistant_text(block["text"]))
            elif block_type == "tool_use":
                name = _token(block.get("name")) or "tool"
                tool_id = _token(block.get("id")) or uuid.uuid4().hex
                self._tool_names[tool_id] = name
                events.append(
                    self._event(
                        InteractionKind.TOOL,
                        ToolPayload(tool_call_id=tool_id, name=name, phase=ToolPhase.START),
                        tool_id,
                    )
                )
        return events

    def _user(self, value: dict[str, Any]) -> list[InteractionEvent]:
        message = value.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            if content and content == self._ignored_prompt:
                self._ignored_prompt = ""
                return []
            if content:
                return [
                    self._message(
                        uuid.uuid4().hex,
                        MessageRole.USER,
                        MessagePhase.COMPLETE,
                        content,
                    )
                ]
            return []
        if not isinstance(content, list):
            return []
        events: list[InteractionEvent] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_id = _token(block.get("tool_use_id")) or "tool"
            name = self._tool_names.get(tool_id, "tool")
            text = _tool_result_text(block.get("content"))
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
            events.append(
                self._event(
                    InteractionKind.TOOL,
                    ToolPayload(
                        tool_call_id=tool_id,
                        name=name,
                        phase=ToolPhase.COMPLETE,
                        exit_code=0 if not block.get("is_error") else 1,
                    ),
                    tool_id,
                )
            )
        return events

    def _result(self, value: dict[str, Any]) -> list[InteractionEvent]:
        self._terminal = True
        self._turn_open = False
        failed = value.get("is_error") is True or value.get("subtype") not in {None, "success"}
        events = [self._assistant_complete()] if self._assistant_open else []
        if failed:
            events.append(
                self._task(InteractionKind.TASK_FAILED, "这一轮失败", "agent_failed")
            )
            return events
        result = value.get("result")
        if isinstance(result, str) and result and not self._assistant_seen:
            events.extend(self._assistant_text(result))
        events.append(self._task(InteractionKind.TASK_COMPLETED, "", ""))
        return events

    def _control_request(self, value: dict[str, Any]) -> list[InteractionEvent]:
        request = value.get("request")
        request_id = _token(value.get("request_id"))
        if not isinstance(request, dict) or not request_id:
            return []
        if request.get("subtype") != "can_use_tool":
            return []
        name = _token(request.get("tool_name")) or "tool"
        if len(self.pending_approvals) >= 8:
            return []
        tool_input = request.get("input")
        self.pending_approvals[request_id] = {
            "input": tool_input if isinstance(tool_input, dict) else {}
        }
        occurred_at = self._now()
        return [
            self._event(
                InteractionKind.APPROVAL_REQUEST,
                ApprovalRequestPayload(
                    request_id=request_id,
                    summary=f"请求使用工具 {name}",
                    expires_at=occurred_at + _APPROVAL_WINDOW_MS,
                ),
                request_id,
                occurred_at=occurred_at,
            )
        ]

    def _assistant_text(self, text: str) -> list[InteractionEvent]:
        self._assistant_seen = True
        if self._assistant_open and self._assistant_id:
            self._assistant_open = False
            return [
                self._message(
                    self._assistant_id,
                    MessageRole.ASSISTANT,
                    MessagePhase.COMPLETE,
                    text,
                )
            ]
        return [self._assistant_delta(text), self._assistant_complete()]

    def _assistant_delta(self, text: str) -> InteractionEvent:
        if not self._assistant_id:
            self._assistant_id = uuid.uuid4().hex
        self._assistant_open = True
        self._assistant_seen = True
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
        payload: MessagePayload | ToolPayload | ApprovalRequestPayload | TaskPayload,
        correlation_id: str,
        occurred_at: int | None = None,
    ) -> InteractionEvent:
        event = InteractionEvent(
            kind=kind,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            source="claude-stream",
            source_version=self.source_version,
            sequence=self.sequence,
            payload=payload,
            correlation_id=correlation_id,
            occurred_at=occurred_at or 0,
        )
        self.sequence += 1
        return event

    @staticmethod
    def _now() -> int:
        from time import time

        return int(time() * 1000)


def _tool_result_text(value: object) -> str:
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return _clip("".join(parts))
    return ""


def _token(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:128]


def _clip(value: str) -> str:
    if len(value) <= _MAX_TEXT:
        return value
    return value[: _MAX_TEXT - 1] + "…"
