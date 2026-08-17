from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from .client import send_event
from .event import AgentEvent, AgentState
from .paths import default_socket_path


EVENT_STATES = {
    "SessionStart": AgentState.IDLE,
    "UserPromptSubmit": AgentState.THINKING,
    "PreToolUse": AgentState.RUNNING_TOOL,
    "PostToolUse": AgentState.THINKING,
    "PermissionRequest": AgentState.WAITING_APPROVAL,
    "SubagentStart": AgentState.RUNNING_TOOL,
    "SubagentStop": AgentState.THINKING,
    "Stop": AgentState.COMPLETED,
}


def event_from_hook(payload: dict[str, Any], slot: int, label: str) -> AgentEvent:
    hook_name = str(
        payload.get("hook_event_name")
        or payload.get("event_name")
        or payload.get("event")
        or ""
    )
    state = EVENT_STATES.get(hook_name, AgentState.THINKING)
    session_id = str(payload.get("session_id") or payload.get("thread_id") or os.getpid())
    agent_id = str(payload.get("agent_id") or f"codex:{session_id}")
    return AgentEvent(agent_id=agent_id, slot=slot, state=state, label=label or "codex")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Forward a Codex hook event to DeskHelm")
    parser.add_argument("--slot", type=int, default=0)
    parser.add_argument("--label", default="codex")
    parser.add_argument("--socket", type=Path, default=default_socket_path())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be a JSON object")
        send_event(event_from_hook(payload, args.slot, args.label), args.socket)
    except (ValueError, json.JSONDecodeError, ConnectionError) as error:
        print(f"deskhelm-codex-hook: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
