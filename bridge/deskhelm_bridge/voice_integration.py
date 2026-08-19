from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING
import uuid

try:
    from deskhelm_voice import (
        SpeechItem,
        SpeechPriority,
        Transcript,
        VoiceGateway,
        VoiceTarget,
    )
except ModuleNotFoundError as error:
    if error.name != "deskhelm_voice":
        raise
    from voice.deskhelm_voice import (
        SpeechItem,
        SpeechPriority,
        Transcript,
        VoiceGateway,
        VoiceTarget,
    )

from .control import (
    ControlCommand,
    ControlKind,
    PressPttPayload,
    ReleasePttPayload,
    SpeakPayload,
    StopSpeakingPayload,
    SubmitPromptPayload,
)
from .control_result import ControlResultStatus
from .control_router import ControlRouter
from .interaction import (
    InteractionEvent,
    InteractionKind,
    MessagePayload,
    MessagePhase,
    MessageRole,
)
from .interaction_subscription import InteractionHub


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(slots=True)
class VoiceBridgeIntegration:
    voice_gateway: VoiceGateway
    control_router: ControlRouter
    interaction_hub: InteractionHub
    prompt_expiry_ms: int = 30_000
    _unregister: list[Callable[[], None]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.prompt_expiry_ms < 1:
            raise ValueError("prompt_expiry_ms must be at least 1")

    def register(self) -> None:
        if self._unregister:
            raise RuntimeError("voice integration is already registered")
        unregister: list[Callable[[], None]] = []
        try:
            unregister.append(
                self.voice_gateway.register_prompt_sink(self.submit_transcript)
            )
            unregister.append(
                self.control_router.register_handler(
                    ControlKind.SPEAK, self.speak
                )
            )
            unregister.append(
                self.control_router.register_handler(
                    ControlKind.STOP_SPEAKING, self.stop_speaking
                )
            )
            unregister.append(
                self.control_router.register_handler(
                    ControlKind.PRESS_PTT, self.press_ptt
                )
            )
            unregister.append(
                self.control_router.register_handler(
                    ControlKind.RELEASE_PTT, self.release_ptt
                )
            )
            unregister.append(
                self.interaction_hub.subscribe(self.observe_interaction)
            )
        except BaseException:
            for callback in reversed(unregister):
                callback()
            raise
        self._unregister = unregister

    def close(self) -> None:
        unregister = tuple(reversed(self._unregister))
        self._unregister.clear()
        for callback in unregister:
            callback()

    def submit_transcript(
        self, target: VoiceTarget, transcript: Transcript
    ) -> None:
        now_ms = int(time.time() * 1000)
        command = ControlCommand(
            command_id=str(uuid.uuid4()),
            kind=ControlKind.SUBMIT_PROMPT,
            agent_id=target.agent_id,
            session_id=target.session_id,
            project_id=target.project_id,
            issued_by="voice-gateway",
            issued_at=now_ms,
            expires_at=now_ms + self.prompt_expiry_ms,
            idempotency_key=f"voice-transcript:{transcript.transcript_id}",
            payload=SubmitPromptPayload(text=transcript.normalized_text),
        )
        result = self.control_router.route(command, now_ms=now_ms)
        if result.status is not ControlResultStatus.ACCEPTED:
            raise RuntimeError("voice prompt dispatch was rejected")

    def speak(self, command: ControlCommand) -> None:
        payload = command.payload
        if not isinstance(payload, SpeakPayload):
            raise ValueError("speak command has an invalid payload")
        self.voice_gateway.enqueue_speech(
            SpeechItem(
                target=self._target(command),
                text=payload.text,
                priority=SpeechPriority(payload.priority.value),
                interruptible=payload.interruptible,
                speech_id=command.command_id,
            )
        )

    def stop_speaking(self, command: ControlCommand) -> None:
        payload = command.payload
        if not isinstance(payload, StopSpeakingPayload):
            raise ValueError("stop_speaking command has an invalid payload")
        self.voice_gateway.stop_speaking(
            self._target(command), payload.speech_id
        )

    def press_ptt(self, command: ControlCommand) -> None:
        if not isinstance(command.payload, PressPttPayload):
            raise ValueError("press_ptt command has an invalid payload")
        self.voice_gateway.press_ptt(
            self._target(command), activation_id=command.command_id
        )

    def release_ptt(self, command: ControlCommand) -> None:
        payload = command.payload
        if not isinstance(payload, ReleasePttPayload):
            raise ValueError("release_ptt command has an invalid payload")
        released = self.voice_gateway.release_ptt(
            self._target(command), activation_id=payload.press_command_id
        )
        if not released:
            raise RuntimeError("PTT release does not own the active capture")

    def observe_interaction(self, event: InteractionEvent) -> None:
        if event.kind is not InteractionKind.MESSAGE:
            return
        payload = event.payload
        if (
            not isinstance(payload, MessagePayload)
            or payload.role is not MessageRole.ASSISTANT
            or payload.phase is not MessagePhase.COMPLETE
            or not payload.text
        ):
            return
        target = VoiceTarget(event.agent_id, event.session_id, event.project_id)
        try:
            self.voice_gateway.enqueue_speech(
                SpeechItem(target=target, text=payload.text)
            )
        except RuntimeError:
            self.voice_gateway.report_failure(target, "speech_queue_full")

    @staticmethod
    def _target(command: ControlCommand) -> VoiceTarget:
        return VoiceTarget(
            command.agent_id,
            command.session_id,
            command.project_id,
        )
