from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .control_router import ControlRouter
from .interaction import InteractionEvent


class AgentRuntime(Protocol):
    """Long-lived externally driven Agent integration."""

    def start(
        self,
        publish_interaction: Callable[[InteractionEvent], None],
        control_router: ControlRouter,
    ) -> None: ...

    def close(self) -> None: ...
