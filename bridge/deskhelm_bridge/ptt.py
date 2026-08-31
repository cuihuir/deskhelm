from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Callable
import uuid

from .control import ControlCommand, ControlKind, PressPttPayload, ReleasePttPayload


RIGHT_ALT_KEY = "KEY_RIGHTALT"


@dataclass(slots=True)
class RightAltPttController:
    """Convert observed right-Alt edges into correlated PTT controls."""

    agent_id: str
    session_id: str
    project_id: str
    dispatch: Callable[[ControlCommand], bool]
    issued_by: str = "deskhelm-right-alt-ptt"
    command_ttl_ms: int = 5_000
    _press_command_id: str = field(default="", init=False, repr=False)

    def observe(self, key: str, pressed: bool) -> bool:
        if key != RIGHT_ALT_KEY:
            return False
        if pressed:
            if self._press_command_id:
                return False
            command = self._command(ControlKind.PRESS_PTT, PressPttPayload())
            if not self.dispatch(command):
                return False
            self._press_command_id = command.command_id
            return True
        if not self._press_command_id:
            return False
        press_command_id = self._press_command_id
        self._press_command_id = ""
        return self.dispatch(
            self._command(
                ControlKind.RELEASE_PTT,
                ReleasePttPayload(press_command_id=press_command_id),
            )
        )

    def _command(self, kind: ControlKind, payload: PressPttPayload | ReleasePttPayload) -> ControlCommand:
        issued_at = int(time.time() * 1000)
        return ControlCommand(
            kind=kind,
            agent_id=self.agent_id,
            session_id=self.session_id,
            project_id=self.project_id,
            issued_by=self.issued_by,
            issued_at=issued_at,
            expires_at=issued_at + self.command_ttl_ms,
            idempotency_key=str(uuid.uuid4()),
            payload=payload,
        )
