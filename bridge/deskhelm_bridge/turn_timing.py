from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import time

from .interaction import InteractionEvent, InteractionKind


@dataclass(frozen=True, slots=True)
class VoiceAgentTurnTiming:
    """Numeric-only timing for one Voice-originated Agent turn."""

    agent_id: str
    session_id: str
    project_id: str
    terminal_kind: InteractionKind
    release_to_transcript_ms: float | None
    release_to_notification_ready_ms: float | None
    release_to_agent_terminal_ms: float | None
    release_to_speech_started_ms: float | None
    release_to_tts_audio_ready_ms: float | None
    release_to_playback_started_ms: float | None
    dispatch_to_notification_ready_ms: float | None
    dispatch_to_agent_terminal_ms: float
    dispatch_to_speech_started_ms: float | None
    dispatch_to_tts_audio_ready_ms: float | None
    dispatch_to_playback_started_ms: float | None
    notification_ready_to_speech_started_ms: float | None
    speech_started_to_tts_audio_ready_ms: float | None
    tts_audio_ready_to_playback_started_ms: float | None


@dataclass(slots=True)
class _TurnRecord:
    dispatched_ns: int
    released_ns: int | None = None
    transcript_ready_ns: int | None = None
    notification_ready_ns: int | None = None
    speech_started_ns: int | None = None
    tts_audio_ready_ns: int | None = None
    playback_started_ns: int | None = None
    agent_terminal_ns: int | None = None
    agent_terminal_kind: InteractionKind | None = None
    voice_terminal: bool = False


@dataclass(slots=True)
class VoiceAgentTurnTimer:
    result_sink: Callable[[VoiceAgentTurnTiming], None]
    monotonic_ns: Callable[[], int] = time.monotonic_ns
    max_active_turns: int = 8
    _turns: dict[tuple[str, str, str, str], _TurnRecord] = field(
        default_factory=dict, init=False, repr=False
    )
    _pending_voice: dict[
        tuple[str, str, str], tuple[int | None, int | None]
    ] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.max_active_turns < 1:
            raise ValueError("max_active_turns must be at least 1")

    def begin(self, target, turn_id: str) -> None:
        key = self._key(target, turn_id)
        if len(self._turns) >= self.max_active_turns and key not in self._turns:
            raise RuntimeError("voice turn timing capacity is exhausted")
        released_ns, transcript_ready_ns = self._pending_voice.pop(
            key[:3], (None, None)
        )
        self._turns[key] = _TurnRecord(
            self.monotonic_ns(),
            released_ns=released_ns,
            transcript_ready_ns=transcript_ready_ns,
        )

    def mark_release(self, target) -> None:
        key = (target.agent_id, target.session_id, target.project_id)
        if len(self._pending_voice) >= self.max_active_turns and key not in self._pending_voice:
            raise RuntimeError("voice turn timing capacity is exhausted")
        self._pending_voice[key] = (self.monotonic_ns(), None)

    def discard_pending(self, target) -> None:
        key = (target.agent_id, target.session_id, target.project_id)
        self._pending_voice.pop(key, None)

    def discard(self, target, turn_id: str) -> None:
        self._turns.pop(self._key(target, turn_id), None)

    def mark_notification_ready(self, target, turn_id: str) -> None:
        record = self._turns.get(self._key(target, turn_id))
        if record is not None:
            record.notification_ready_ns = self.monotonic_ns()

    def mark_voice_terminal(self, target, turn_id: str) -> None:
        key = self._key(target, turn_id)
        record = self._turns.get(key)
        if record is not None:
            record.voice_terminal = True
            self._emit_if_ready(key, record)

    def observe_voice(self, event) -> None:
        kind = getattr(event, "kind", None)
        if getattr(kind, "value", "") == "transcript_ready":
            key = (event.target.agent_id, event.target.session_id, event.target.project_id)
            pending = self._pending_voice.get(key)
            if pending is not None:
                self._pending_voice[key] = (pending[0], self.monotonic_ns())
            return
        if getattr(kind, "value", "") not in {
            "speech_started",
            "tts_audio_ready",
            "playback_started",
            "speech_completed",
            "speech_cancelled",
            "failure",
        }:
            return
        speech_id = getattr(event, "speech_id", "")
        if not speech_id:
            return
        record = self._turns.get(self._key(event.target, speech_id))
        if record is None:
            return
        now = self.monotonic_ns()
        if kind.value == "speech_started" and record.speech_started_ns is None:
            record.speech_started_ns = now
        elif kind.value == "tts_audio_ready" and record.tts_audio_ready_ns is None:
            record.tts_audio_ready_ns = now
        elif kind.value == "playback_started" and record.playback_started_ns is None:
            record.playback_started_ns = now
        elif kind.value in {"speech_completed", "speech_cancelled", "failure"}:
            record.voice_terminal = True
        self._emit_if_ready(self._key(event.target, speech_id), record)

    def observe_interaction(self, event: InteractionEvent) -> None:
        if event.kind not in {InteractionKind.TASK_COMPLETED, InteractionKind.TASK_FAILED}:
            return
        if not event.correlation_id:
            return
        key = (event.agent_id, event.session_id, event.project_id, event.correlation_id)
        record = self._turns.get(key)
        if record is None:
            return
        record.agent_terminal_ns = self.monotonic_ns()
        record.agent_terminal_kind = event.kind
        if (
            event.kind is InteractionKind.TASK_FAILED
            or record.notification_ready_ns is None
        ):
            record.voice_terminal = True
        self._emit_if_ready(key, record)

    def _emit_if_ready(
        self,
        key: tuple[str, str, str, str],
        record: _TurnRecord,
    ) -> None:
        if record.agent_terminal_ns is None or not record.voice_terminal:
            return
        terminal_kind = record.agent_terminal_kind
        if terminal_kind is None:
            return
        self._turns.pop(key, None)
        self.result_sink(
            VoiceAgentTurnTiming(
                agent_id=key[0],
                session_id=key[1],
                project_id=key[2],
                terminal_kind=terminal_kind,
                release_to_transcript_ms=self._between_optional(
                    record.released_ns, record.transcript_ready_ns
                ),
                release_to_notification_ready_ms=self._between_optional(
                    record.released_ns, record.notification_ready_ns
                ),
                release_to_agent_terminal_ms=self._between_optional(
                    record.released_ns, record.agent_terminal_ns
                ),
                release_to_speech_started_ms=self._between_optional(
                    record.released_ns, record.speech_started_ns
                ),
                release_to_tts_audio_ready_ms=self._between_optional(
                    record.released_ns, record.tts_audio_ready_ns
                ),
                release_to_playback_started_ms=self._between_optional(
                    record.released_ns, record.playback_started_ns
                ),
                dispatch_to_notification_ready_ms=self._optional_milliseconds(
                    record.dispatched_ns, record.notification_ready_ns
                ),
                dispatch_to_agent_terminal_ms=self._milliseconds(
                    record.dispatched_ns, record.agent_terminal_ns
                ),
                dispatch_to_speech_started_ms=self._optional_milliseconds(
                    record.dispatched_ns, record.speech_started_ns
                ),
                dispatch_to_tts_audio_ready_ms=self._optional_milliseconds(
                    record.dispatched_ns, record.tts_audio_ready_ns
                ),
                dispatch_to_playback_started_ms=self._optional_milliseconds(
                    record.dispatched_ns, record.playback_started_ns
                ),
                notification_ready_to_speech_started_ms=self._between_optional(
                    record.notification_ready_ns, record.speech_started_ns
                ),
                speech_started_to_tts_audio_ready_ms=self._between_optional(
                    record.speech_started_ns, record.tts_audio_ready_ns
                ),
                tts_audio_ready_to_playback_started_ms=self._between_optional(
                    record.tts_audio_ready_ns, record.playback_started_ns
                ),
            )
        )

    @staticmethod
    def _key(target, turn_id: str) -> tuple[str, str, str, str]:
        if not isinstance(turn_id, str) or not turn_id:
            raise ValueError("turn_id must not be empty")
        return (target.agent_id, target.session_id, target.project_id, turn_id)

    @staticmethod
    def _milliseconds(start_ns: int, end_ns: int) -> float:
        return (end_ns - start_ns) / 1_000_000

    @classmethod
    def _optional_milliseconds(cls, start_ns: int, end_ns: int | None) -> float | None:
        return None if end_ns is None else cls._milliseconds(start_ns, end_ns)

    @classmethod
    def _between_optional(
        cls, start_ns: int | None, end_ns: int | None
    ) -> float | None:
        if start_ns is None or end_ns is None:
            return None
        return cls._milliseconds(start_ns, end_ns)
