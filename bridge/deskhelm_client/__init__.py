"""The protocol v2 client runtime every surface shares (ADR 0043 §4.6)."""

from .connection import BridgeClient, CommandResult, ConsoleChange, ConsoleState
from .texts import activity_text, outcome_text, result_text

__all__ = [
    "BridgeClient",
    "CommandResult",
    "ConsoleChange",
    "ConsoleState",
    "activity_text",
    "outcome_text",
    "result_text",
]
