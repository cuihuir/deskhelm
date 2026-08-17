"""Local Bridge for DeskHelm."""

from .event import AgentEvent, AgentState, ProtocolError
from .interaction import InteractionEvent, InteractionKind

__all__ = [
    "AgentEvent",
    "AgentState",
    "InteractionEvent",
    "InteractionKind",
    "ProtocolError",
]
