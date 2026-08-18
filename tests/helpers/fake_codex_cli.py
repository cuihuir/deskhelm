#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time


if "--version" in sys.argv:
    print("codex-cli fake-1.0")
    raise SystemExit(0)

prompt = sys.stdin.read()
if prompt == "wait":
    time.sleep(30)
    raise SystemExit(0)
if prompt == "malformed":
    print('{"type":')
    raise SystemExit(0)
if prompt == "nonzero":
    raise SystemExit(7)

is_resume = "resume" in sys.argv
thread_id = "provider-session-1"
events = [
    {"type": "thread.started", "thread_id": thread_id},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {
            "id": "message-1",
            "type": "agent_message",
            "text": "resumed response" if is_resume else "initial response",
        },
    },
    {"type": "turn.completed", "usage": {}},
]
for event in events:
    print(json.dumps(event, separators=(",", ":")), flush=True)
