"""Fold one session's interaction events into the items a transcript shows.

The operator interaction stream carries message deltas, tool phases,
approval requests, and turn results as separate events. A surface shows the
items they build: one message with its accumulated text, one tool call with
its title, output, and exit code. ``Transcript`` folds events into such items
and reports every change as a patch, so a surface can send the items once
and then only what changes:

- ``{"op": "upsert", "item": {...}}`` adds or replaces a whole item;
- ``{"op": "append", "id": ..., "field": "text" | "output", "text": ...}``
  extends a streaming message or a tool's output;
- ``{"op": "remove", "id": ...}`` drops an item that fell out of the bounds.

Items keep the order they were first seen in. Every bound is fixed, and the
transcript is held in memory only (ADR 0043 D1, ADR 0044).
"""

from __future__ import annotations

from collections import OrderedDict
import copy
from typing import Any

MAX_ITEMS = 200
# Characters across the strings of every item, an approximate memory bound.
MAX_CHARS = 512 * 1024
MAX_MESSAGE_CHARS = 64 * 1024
MAX_TITLE_CHARS = 2 * 1024
MAX_OUTPUT_CHARS = 32 * 1024
MAX_SUMMARY_CHARS = 512
MAX_PREVIEW_CHARS = 160

_ROLES = frozenset({"user", "assistant", "system"})


class Transcript:
    """The bounded items of one session, built from its interaction events."""

    def __init__(self, *, max_items: int = MAX_ITEMS, max_chars: int = MAX_CHARS) -> None:
        self.max_items = max_items
        self.max_chars = max_chars
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._sizes: dict[str, int] = {}
        self._chars = 0
        self._seq = 0

    def items(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self._items.values()]

    def item(self, item_id: str) -> dict[str, Any] | None:
        item = self._items.get(item_id)
        return None if item is None else copy.deepcopy(item)

    def preview(self) -> str:
        """One line for a session list: the latest message, or else a tool."""
        for item in reversed(self._items.values()):
            if item["type"] == "message" and item["text"].strip():
                return _one_line(item["text"])
            if item["type"] == "tool":
                return _one_line(item["title"] or item["kind"])
        return ""

    def apply(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """Fold one interaction event and return the patches it caused."""
        kind = event.get("kind")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return []
        if kind == "message":
            patches = self._message(event, payload)
        elif kind == "tool":
            patches = self._tool(event, payload)
        elif kind == "approval_request":
            patches = self._approval(event, payload)
        elif kind == "user_input_required":
            patches = self._input(event, payload)
        elif kind in ("task_completed", "task_failed"):
            patches = self._turn(event, payload, failed=kind == "task_failed")
        else:
            return []
        return patches + self._trim()

    # Kinds -------------------------------------------------------------

    def _message(self, event: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        item_id = "m:" + _reference(event)
        text = _text(payload.get("text"))
        role = payload.get("role") if payload.get("role") in _ROLES else "assistant"
        item = self._items.get(item_id)
        created = item is None
        if item is None:
            item = self._create(item_id, "message", event, role=role, text="", streaming=True)
        item["role"] = role
        phase = payload.get("phase")
        if phase == "delta":
            if not item["streaming"]:
                return [self._upsert(item)] if created else []
            chunk = text[: max(0, MAX_MESSAGE_CHARS - len(item["text"]))]
            item["text"] += chunk
            if created or not chunk:
                return [self._upsert(item)] if created else []
            return [self._append(item, "text", chunk)]
        if phase == "complete":
            if text:
                item["text"] = text[:MAX_MESSAGE_CHARS]
            item["streaming"] = False
        return [self._upsert(item)]

    def _tool(self, event: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        item_id = "t:" + (_identity(payload.get("tool_call_id")) or _reference(event))
        text = _text(payload.get("text"))
        item = self._items.get(item_id)
        created = item is None
        if item is None:
            item = self._create(
                item_id, "tool", event, kind="tool", title="", output="", truncated=False,
                status="running", exit_code=None, stderr=False,
            )
        label = _text(payload.get("name"))
        if label and (created or item["kind"] == "tool"):
            # A tool keeps the label it had when it started.
            item["kind"] = label[:128]
        phase = payload.get("phase")
        if phase == "start":
            if text.strip():
                item["title"] = text.strip()[:MAX_TITLE_CHARS]
            return [self._upsert(item)]
        if phase == "output":
            flagged = payload.get("stream") == "stderr" and not item["stderr"]
            if flagged:
                item["stderr"] = True
            if not text:
                return [self._upsert(item)] if created or flagged else []
            output = item["output"] + text
            if len(output) > MAX_OUTPUT_CHARS:
                # Keep the end of the output, where results and errors are.
                item["output"] = output[-MAX_OUTPUT_CHARS:]
                item["truncated"] = True
                return [self._upsert(item)]
            item["output"] = output
            if created or flagged:
                return [self._upsert(item)]
            return [self._append(item, "output", text)]
        if phase == "complete":
            exit_code = payload.get("exit_code")
            if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                exit_code = None
            item["exit_code"] = exit_code
            item["status"] = "failed" if exit_code not in (None, 0) else "done"
        return [self._upsert(item)]

    def _approval(self, event: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        approval_id = _identity(payload.get("request_id"))
        if not approval_id:
            return []
        item_id = "a:" + approval_id
        if item_id in self._items:
            return []
        expires_at = payload.get("expires_at")
        item = self._create(
            item_id, "approval", event, approval_id=approval_id,
            summary=_text(payload.get("summary"))[:MAX_SUMMARY_CHARS],
            expires_at=expires_at if isinstance(expires_at, int) and not isinstance(expires_at, bool) else 0,
        )
        return [self._upsert(item)]

    def _input(self, event: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        item_id = "i:" + _reference(event)
        if item_id in self._items:
            return []
        item = self._create(item_id, "input", event, prompt=_text(payload.get("prompt"))[:MAX_SUMMARY_CHARS])
        return [self._upsert(item)]

    def _turn(self, event: dict[str, Any], payload: dict[str, Any], *, failed: bool) -> list[dict[str, Any]]:
        item_id = "r:" + (_identity(event.get("event_id")) or str(self._seq + 1))
        if item_id in self._items:
            return []
        code = _text(payload.get("error_code"))[:64]
        if not failed:
            status = "completed"
        elif code == "interrupted":
            status = "interrupted"
        else:
            status = "failed"
        item = self._create(
            item_id, "turn", event, status=status, code="" if status != "failed" else code,
            message=_text(payload.get("message"))[:MAX_SUMMARY_CHARS],
        )
        return [self._upsert(item)]

    # Storage -----------------------------------------------------------

    def _create(self, item_id: str, item_type: str, event: dict[str, Any], **fields: Any) -> dict[str, Any]:
        self._seq += 1
        occurred = event.get("occurred_at")
        item = {
            "id": item_id,
            "type": item_type,
            "seq": self._seq,
            "at": occurred if isinstance(occurred, int) and not isinstance(occurred, bool) else 0,
            **fields,
        }
        self._items[item_id] = item
        self._sizes[item_id] = 0
        return item

    def _account(self, item: dict[str, Any]) -> None:
        size = 64 + sum(len(value) for value in item.values() if isinstance(value, str))
        self._chars += size - self._sizes.get(item["id"], 0)
        self._sizes[item["id"]] = size

    def _upsert(self, item: dict[str, Any]) -> dict[str, Any]:
        self._account(item)
        return {"op": "upsert", "item": copy.deepcopy(item)}

    def _append(self, item: dict[str, Any], field: str, text: str) -> dict[str, Any]:
        self._account(item)
        return {"op": "append", "id": item["id"], "field": field, "text": text}

    def _trim(self) -> list[dict[str, Any]]:
        removed = []
        while len(self._items) > 1 and (len(self._items) > self.max_items or self._chars > self.max_chars):
            item_id, _ = self._items.popitem(last=False)
            self._chars -= self._sizes.pop(item_id, 0)
            removed.append({"op": "remove", "id": item_id})
        return removed


def _identity(value: object) -> str:
    return value[:256] if isinstance(value, str) and value.strip() else ""


def _reference(event: dict[str, Any]) -> str:
    return _identity(event.get("correlation_id")) or _identity(event.get("event_id")) or "unknown"


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _one_line(text: str) -> str:
    return " ".join(text.split())[:MAX_PREVIEW_CHARS]
