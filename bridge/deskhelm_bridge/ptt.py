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
    target_resolver: Callable[[], tuple[str, str, str] | None] | None = None
    _press_command_id: str = field(default="", init=False, repr=False)
    _active_target: tuple[str, str, str] | None = field(
        default=None, init=False, repr=False
    )

    def observe(self, key: str, pressed: bool) -> bool:
        if key != RIGHT_ALT_KEY:
            return False
        if pressed:
            if self._press_command_id:
                return False
            target = self._resolve_target()
            if target is None:
                return False
            command = self._command(
                ControlKind.PRESS_PTT, PressPttPayload(), target
            )
            if not self.dispatch(command):
                return False
            self._press_command_id = command.command_id
            self._active_target = target
            return True
        if not self._press_command_id:
            return False
        press_command_id = self._press_command_id
        target = self._active_target
        self._press_command_id = ""
        self._active_target = None
        if target is None:
            return False
        return self.dispatch(
            self._command(
                ControlKind.RELEASE_PTT,
                ReleasePttPayload(press_command_id=press_command_id),
                target,
            )
        )

    def _resolve_target(self) -> tuple[str, str, str] | None:
        if self.target_resolver is not None:
            target = self.target_resolver()
            if target is None:
                return None
            if (
                not isinstance(target, tuple)
                or len(target) != 3
                or any(not isinstance(value, str) or not value.strip() for value in target)
            ):
                raise ValueError("PTT target resolver returned an invalid target")
            return target
        return self.agent_id, self.session_id, self.project_id

    def _command(
        self,
        kind: ControlKind,
        payload: PressPttPayload | ReleasePttPayload,
        target: tuple[str, str, str],
    ) -> ControlCommand:
        issued_at = int(time.time() * 1000)
        return ControlCommand(
            kind=kind,
            agent_id=target[0],
            session_id=target[1],
            project_id=target[2],
            issued_by=self.issued_by,
            issued_at=issued_at,
            expires_at=issued_at + self.command_ttl_ms,
            idempotency_key=str(uuid.uuid4()),
            payload=payload,
        )
