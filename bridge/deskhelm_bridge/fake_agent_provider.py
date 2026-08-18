from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from threading import Event, Lock

from .agent_gateway import (
    AgentProviderEvent,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
)


@dataclass(frozen=True, slots=True)
class FakeRunScript:
    events: tuple[AgentProviderEvent, ...] = ()
    result: AgentRunResult = AgentRunResult(AgentRunStatus.COMPLETED)
    wait_for_cancel: bool = False


@dataclass(slots=True)
class FakeAgentProvider:
    scripts: Iterable[FakeRunScript]
    source: str = "fake-agent"
    source_version: str = "1"
    requests: list[AgentRunRequest] = field(default_factory=list, init=False)
    started: Event = field(default_factory=Event, init=False)
    _scripts: deque[FakeRunScript] = field(init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._scripts = deque(self.scripts)

    def run(
        self,
        request: AgentRunRequest,
        emit: Callable[[AgentProviderEvent], None],
        cancel: Event,
    ) -> AgentRunResult:
        with self._lock:
            if not self._scripts:
                raise RuntimeError("fake provider has no remaining run script")
            script = self._scripts.popleft()
            self.requests.append(request)
        self.started.set()
        for event in script.events:
            emit(event)
        if script.wait_for_cancel:
            if cancel.wait(timeout=5):
                return AgentRunResult(
                    status=AgentRunStatus.CANCELLED,
                    provider_session_id=script.result.provider_session_id,
                )
            return AgentRunResult(
                status=AgentRunStatus.TIMED_OUT,
                error_code="fake_cancel_timeout",
                provider_session_id=script.result.provider_session_id,
            )
        return script.result
