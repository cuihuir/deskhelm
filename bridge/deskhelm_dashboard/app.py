"""The terminal dashboard on protocol v2 (ADR 0043).

One operator connection through deskhelm_client. Pending approvals come from
the console projection, so they survive a reconnect; a dropped Bridge shows a
notice instead of ending the dashboard.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import os
from pathlib import Path
import select
import sys
import termios
from threading import Event, Lock
import time
import tty
from typing import Any, Protocol

from deskhelm_client import BridgeClient, CommandResult, ConsoleChange, ConsoleState, activity_text, outcome_text, result_text
from deskhelm_protocol import SessionKey

MAX_DETAIL_EVENTS = 8
MAX_DETAIL_CHARS = 160
HELP = "j/k 移动 · f 聚焦 · i 中断 · a 批准 · r 拒绝 · q 退出"


class _Client(Protocol):
    connected: bool

    def console(self) -> ConsoleState: ...

    def command(self, kind: str, session: SessionKey, payload: dict[str, Any] | None = None) -> CommandResult: ...


@dataclass(slots=True)
class DashboardState:
    cursor: int = 0
    notice: str = ""
    details: dict[SessionKey, deque[str]] = field(default_factory=dict)


class Dashboard:
    def __init__(self, client: _Client) -> None:
        self.client = client
        self.state = DashboardState()
        self.dirty = Event()
        self._lock = Lock()

    # Bridge callbacks --------------------------------------------------

    def on_console(self, change: ConsoleChange) -> None:
        if change.restarted:
            with self._lock:
                self.state.details.clear()
                self.state.notice = "Bridge 已重启，更早的活动不会补上"
        self.dirty.set()

    def on_interaction(self, event: dict[str, Any], replay: bool) -> None:
        try:
            key = SessionKey.from_dict(event)
        except ValueError:
            return
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        text = payload.get("text") or payload.get("summary") or payload.get("prompt") or ""
        line = f"{event.get('kind', 'event')}: {' '.join(str(text).split())}"[:MAX_DETAIL_CHARS]
        with self._lock:
            self.state.details.setdefault(key, deque(maxlen=MAX_DETAIL_EVENTS)).append(line)
        self.dirty.set()

    def on_connection(self, connected: bool) -> None:
        with self._lock:
            if not connected:
                self.state.notice = result_text("bridge_unavailable")
            elif self.state.notice == result_text("bridge_unavailable"):
                self.state.notice = ""
        self.dirty.set()

    # Keys --------------------------------------------------------------

    def rows(self) -> list[tuple[SessionKey, dict[str, Any]]]:
        sessions = self.client.console().sessions
        return sorted(sessions.items(), key=lambda item: (item[0].project_id, item[0].agent_id, item[0].session_id))

    def handle_key(self, key: bytes) -> bool:
        """Apply one key; False means quit."""
        if key in {b"q", b"Q", b"\x03"}:
            return False
        rows = self.rows()
        with self._lock:
            if rows:
                self.state.cursor = min(self.state.cursor, len(rows) - 1)
            cursor = self.state.cursor
        if key in {b"j", b"\x1b[B"} and rows:
            with self._lock:
                self.state.cursor = min(cursor + 1, len(rows) - 1)
        elif key in {b"k", b"\x1b[A"} and rows:
            with self._lock:
                self.state.cursor = max(cursor - 1, 0)
        elif key in {b"f", b"F"} and rows:
            self._send("focus", rows[cursor][0])
        elif key in {b"i", b"I"} and rows:
            self._send("interrupt", rows[cursor][0])
        elif key in {b"a", b"A", b"r", b"R"} and rows:
            self._decide("approve" if key in {b"a", b"A"} else "reject", rows[cursor][0])
        elif key == b"?":
            self._say(HELP)
        self.dirty.set()
        return True

    def _decide(self, kind: str, target: SessionKey) -> None:
        tickets = self.client.console().approvals(target)
        if not tickets:
            self._say("这个会话没有等待批准的请求")
            return
        if len(tickets) > 1:
            self._say(f"这个会话有 {len(tickets)} 条请求，先处理第一条")
        self._send(kind, target, {"approval_id": tickets[0]["approval_id"]})

    def _send(self, kind: str, target: SessionKey, payload: dict[str, Any] | None = None) -> None:
        result = self.client.command(kind, target, payload)
        self._say(result_text(result.code))

    def _say(self, notice: str) -> None:
        with self._lock:
            self.state.notice = notice

    # Rendering ---------------------------------------------------------

    def render(self, *, now_ms: int | None = None) -> list[str]:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        console = self.client.console()
        rows = self.rows()
        with self._lock:
            cursor = min(self.state.cursor, len(rows) - 1) if rows else 0
            notice = self.state.notice
            details = {key: list(lines) for key, lines in self.state.details.items()}
        lines = [f"DeskHelm Dashboard · {HELP}", ""]
        lines.append("会话" + ("" if self.client.connected else "（未连上 Bridge）"))
        if not rows:
            lines.append("  （没有会话）")
        for index, (key, view) in enumerate(rows):
            marker = ">" if index == cursor else " "
            focus = "*" if key == console.focus else " "
            runtime = (view.get("runtime") or {}).get("runtime_name", "")
            pending = len(console.approvals(key, now_ms=now))
            badge = f"  待批准 {pending}" if pending else ""
            lines.append(f"{marker}{focus} {key.project_id[:20]:<20} {key.agent_id[:10]:<10} {runtime[:16]:<16} {activity_text(view)}{badge}")
        lines.extend(["", "选中"])
        if not rows:
            lines.append("  无")
        else:
            key, view = rows[cursor]
            runtime = view.get("runtime") or {}
            lines.append(f"  {key.agent_id} / {key.session_id} / {key.project_id}")
            lines.append(f"  {runtime.get('runtime_name', '')} {runtime.get('runtime_version', '')}".rstrip())
            outcome = outcome_text(view.get("last_outcome"))
            if outcome:
                lines.append(f"  上一轮：{outcome}")
            for ticket in view.get("approvals", []):
                remaining = max(0, (int(ticket.get("expires_at", 0)) - now) // 1000)
                how = "可在这里处理" if ticket.get("answerable") else "请在 Agent 里处理"
                lines.append(f"  {ticket.get('kind')}（{remaining} 秒，{how}）：{ticket.get('summary', '')[:MAX_DETAIL_CHARS]}")
            lines.append("  最近活动：")
            lines.extend(f"    {entry}" for entry in details.get(key) or ["（打开后还没有活动）"])
        if notice:
            lines.extend(["", f"提示：{notice}"])
        return lines


def run_dashboard(socket_path: Path, *, client_id: str = "deskhelm-dashboard") -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("deskhelm dashboard: needs an interactive terminal", file=sys.stderr)
        return 2
    holder: dict[str, Dashboard] = {}
    client = BridgeClient(
        socket_path,
        client_id=client_id,
        interaction=True,
        on_console=lambda change: holder["dashboard"].on_console(change),
        on_interaction=lambda event, replay: holder["dashboard"].on_interaction(event, replay),
        on_connection=lambda connected: holder["dashboard"].on_connection(connected),
    )
    dashboard = holder["dashboard"] = Dashboard(client)
    descriptor = sys.stdin.fileno()
    original = termios.tcgetattr(descriptor)
    client.start()
    try:
        tty.setcbreak(descriptor)
        dashboard.dirty.set()
        last_render = 0.0
        while True:
            readable, _, _ = select.select([sys.stdin], [], [], 0.25)
            if readable and not dashboard.handle_key(os.read(descriptor, 8)):
                return 0
            # Approval timers tick once a second even without Bridge changes.
            if dashboard.dirty.is_set() or time.monotonic() - last_render >= 1.0:
                dashboard.dirty.clear()
                sys.stdout.write("\033[2J\033[H" + "\n".join(dashboard.render()) + "\n")
                sys.stdout.flush()
                last_render = time.monotonic()
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original)
        client.close()
        sys.stdout.write("\033[0m\033[2J\033[H")
        sys.stdout.flush()
