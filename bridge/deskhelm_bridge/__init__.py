"""Local Bridge for DeskHelm."""

from .event import AgentEvent, AgentState, ProtocolError
from .interaction import InteractionEvent, InteractionKind
from .client import send_negotiated_event
from .transport import ClientHello, ClientRole, ServerHello

__all__ = [
    "AgentEvent",
    "AgentState",
    "ClientHello",
    "ClientRole",
    "InteractionEvent",
    "InteractionKind",
    "ProtocolError",
    "ServerHello",
    "send_negotiated_event",
]
