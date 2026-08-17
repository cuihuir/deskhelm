from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import json
import time
from typing import Any


PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    pass


class AgentState(StrEnum):
    OFFLINE = "offline"
    IDLE = "idle"
    THINKING = "thinking"
    RUNNING_TOOL = "running_tool"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    agent_id: str
    slot: int
    state: AgentState
    label: str = ""
    progress: float | None = None
    updated_at: int = 0
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not self.agent_id.strip():
            raise ProtocolError("agent_id must not be empty")
        if self.slot < 0:
            raise ProtocolError("slot must be zero or greater")
        if self.progress is not None and not 0 <= self.progress <= 1:
            raise ProtocolError("progress must be between 0 and 1")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(
                f"unsupported protocol_version {self.protocol_version}; expected {PROTOCOL_VERSION}"
            )
        if not self.updated_at:
            object.__setattr__(self, "updated_at", int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AgentEvent:
        required = {"agent_id", "slot", "state"}
        missing = required - payload.keys()
        if missing:
            raise ProtocolError(f"missing required fields: {', '.join(sorted(missing))}")

        try:
            state = AgentState(payload["state"])
            slot = int(payload["slot"])
            progress = payload.get("progress")
            if progress is not None:
                progress = float(progress)
            return cls(
                agent_id=str(payload["agent_id"]),
                slot=slot,
                state=state,
                label=str(payload.get("label", "")),
                progress=progress,
                updated_at=int(payload.get("updated_at", 0)),
                protocol_version=int(payload.get("protocol_version", PROTOCOL_VERSION)),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ProtocolError):
                raise
            raise ProtocolError(str(error)) from error

    @classmethod
    def from_json(cls, line: str) -> AgentEvent:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ProtocolError(f"invalid JSON: {error.msg}") from error
        if not isinstance(payload, dict):
            raise ProtocolError("event must be a JSON object")
        return cls.from_dict(payload)
