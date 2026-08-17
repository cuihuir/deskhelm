from __future__ import annotations

from dataclasses import dataclass
from typing import TextIO

from .event import AgentEvent, AgentState


COLORS = {
    AgentState.OFFLINE: "\033[90m",
    AgentState.IDLE: "\033[37m",
    AgentState.THINKING: "\033[94m",
    AgentState.RUNNING_TOOL: "\033[96m",
    AgentState.WAITING_APPROVAL: "\033[93m",
    AgentState.WAITING_USER: "\033[95m",
    AgentState.COMPLETED: "\033[92m",
    AgentState.FAILED: "\033[91m",
}
RESET = "\033[0m"


@dataclass(slots=True)
class SlotPanel:
    stream: TextIO
    color: bool = True
    live: bool = True

    def render(self, events: tuple[AgentEvent, ...], changed: AgentEvent | None = None) -> None:
        if not self.live:
            if changed is not None:
                self.stream.write(self._plain_line(changed) + "\n")
                self.stream.flush()
            return

        lines = ["agent-io · Phase 0", ""]
        lines.extend(self._slot_line(event) for event in events)
        self.stream.write("\033[2J\033[H" + "\n".join(lines) + "\n")
        self.stream.flush()

    def on_state_change(
        self, changed: AgentEvent, snapshot: tuple[AgentEvent, ...]
    ) -> None:
        self.render(snapshot, changed)

    def _slot_line(self, event: AgentEvent) -> str:
        marker = "●"
        if self.color:
            marker = f"{COLORS[event.state]}{marker}{RESET}"
        label = event.label or event.agent_id
        progress = "" if event.progress is None else f" {event.progress:>4.0%}"
        return f"[{event.slot + 1}] {marker} {label:<20.20} {event.state.value:<18}{progress}"

    @staticmethod
    def _plain_line(event: AgentEvent) -> str:
        label = event.label or event.agent_id
        return f"slot={event.slot} agent={event.agent_id} label={label} state={event.state.value}"
