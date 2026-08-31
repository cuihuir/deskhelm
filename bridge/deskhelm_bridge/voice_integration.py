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
        VoiceEvent,
        VoiceEventKind,
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
        VoiceEvent,
        VoiceEventKind,
        VoiceGateway,
        VoiceTarget,
    )

from .control import (
    ApprovalDecisionPayload,
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
    AttentionPayload,
    AttentionReason,
    InteractionEvent,
    InteractionKind,
    MessagePayload,
    MessagePhase,
    MessageRole,
    TaskPayload,
)
from .interaction_subscription import InteractionHub
from .session_registry import SessionKey
from .turn_timing import VoiceAgentTurnTimer


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(slots=True)
class VoiceBridgeIntegration:
    voice_gateway: VoiceGateway
    control_router: ControlRouter
    interaction_hub: InteractionHub
    prompt_expiry_ms: int = 30_000
    turn_timer: VoiceAgentTurnTimer | None = None
    _unregister: list[Callable[[], None]] = field(default_factory=list, init=False)
    _previous_event_sink: Callable[[object], None] | None = field(
        default=None, init=False, repr=False
    )
    _voice_sequence: int = field(default=0, init=False)
    _active_turn_ids: dict[VoiceTarget, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _active_attention: set[tuple[VoiceTarget, AttentionReason]] = field(
        default_factory=set, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.prompt_expiry_ms < 1:
            raise ValueError("prompt_expiry_ms must be at least 1")

    def register(self) -> None:
        if self._unregister:
            raise RuntimeError("voice integration is already registered")
        unregister: list[Callable[[], None]] = []
        try:
            self._previous_event_sink = self.voice_gateway.event_sink
            self.voice_gateway.event_sink = self.observe_voice_event
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
            self.voice_gateway.event_sink = self._previous_event_sink
            self._previous_event_sink = None
            for callback in reversed(unregister):
                callback()
            raise
        self._unregister = unregister

    def close(self) -> None:
        unregister = tuple(reversed(self._unregister))
        self._unregister.clear()
        for callback in unregister:
            callback()
        if self.voice_gateway.event_sink == self.observe_voice_event:
            self.voice_gateway.event_sink = self._previous_event_sink
        self._previous_event_sink = None
        self._active_attention.clear()

    def submit_transcript(
        self, target: VoiceTarget, transcript: Transcript
    ) -> None:
        now_ms = int(time.time() * 1000)
        session = SessionKey(
            target.agent_id,
            target.session_id,
            target.project_id,
        )
        pending_approvals = self.control_router.pending_approvals_for(
            session,
            now_ms=now_ms,
        )
        decision_kind = _VOICE_APPROVAL_PHRASES.get(
            transcript.normalized_text.strip()
        )
        if decision_kind is not None and pending_approvals:
            if len(pending_approvals) != 1:
                raise RuntimeError(
                    "voice approval requires exactly one pending request"
                )
            pending = pending_approvals[0]
            command = ControlCommand(
                command_id=str(uuid.uuid4()),
                kind=decision_kind,
                agent_id=target.agent_id,
                session_id=target.session_id,
                project_id=target.project_id,
                issued_by="voice-gateway",
                issued_at=now_ms,
                expires_at=pending.expires_at,
                idempotency_key=(
                    f"voice-approval:{transcript.transcript_id}:"
                    f"{pending.request_id}:{decision_kind.value}"
                ),
                payload=ApprovalDecisionPayload(
                    request_id=pending.request_id,
                    summary=pending.summary,
                    request_expires_at=pending.expires_at,
                ),
            )
            result = self.control_router.route(command, now_ms=now_ms)
            if result.status is not ControlResultStatus.ACCEPTED:
                raise RuntimeError("voice approval dispatch was rejected")
            return

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
        starts_timed_turn = (
            self.turn_timer is not None
            and target not in self._active_turn_ids
        )
        if starts_timed_turn:
            self.turn_timer.begin(target, command.command_id)
            self._active_turn_ids[target] = command.command_id
        result = self.control_router.route(command, now_ms=now_ms)
        if result.status is not ControlResultStatus.ACCEPTED:
            if starts_timed_turn and self.turn_timer is not None:
                self.turn_timer.discard(target, command.command_id)
                self._active_turn_ids.pop(target, None)
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
        target = self._target(command)
        if self.turn_timer is not None:
            self.turn_timer.mark_release(target)
        released = self.voice_gateway.release_ptt(
            target, activation_id=payload.press_command_id
        )
        if not released:
            if self.turn_timer is not None:
                self.turn_timer.discard_pending(target)
            raise RuntimeError("PTT release does not own the active capture")

    def observe_interaction(self, event: InteractionEvent) -> None:
        target = VoiceTarget(event.agent_id, event.session_id, event.project_id)
        if event.kind is InteractionKind.TASK_COMPLETED:
            self._clear_attention(target)
            speech_id = self._active_turn_ids.pop(target, "")
            if self.turn_timer is not None and speech_id:
                self.turn_timer.mark_notification_ready(target, speech_id)
            if self.turn_timer is not None:
                self.turn_timer.observe_interaction(event)
            self._enqueue_notification(
                target, _COMPLETION_NOTIFICATION, speech_id
            )
            return

        if self.turn_timer is not None:
            self.turn_timer.observe_interaction(event)

        if event.kind is InteractionKind.TASK_FAILED:
            self._clear_attention(target)
            self._active_turn_ids.pop(target, None)
            payload = event.payload
            notification = _FAILURE_NOTIFICATION
            if isinstance(payload, TaskPayload):
                notification = _TASK_FAILURE_NOTIFICATIONS.get(
                    payload.error_code,
                    notification,
                )
            self._enqueue_notification(target, notification)
        elif event.kind is InteractionKind.APPROVAL_REQUEST:
            self._activate_attention(target, AttentionReason.APPROVAL)
        elif event.kind is InteractionKind.ATTENTION:
            payload = event.payload
            if not isinstance(payload, AttentionPayload):
                return
            if payload.active:
                self._activate_attention(target, payload.reason)
            else:
                self._active_attention.discard((target, payload.reason))
        elif event.kind is InteractionKind.USER_INPUT_REQUIRED:
            self._enqueue_notification(target, _INPUT_NOTIFICATION)

    def _activate_attention(
        self, target: VoiceTarget, reason: AttentionReason
    ) -> None:
        key = (target, reason)
        if key in self._active_attention:
            return
        self._active_attention.add(key)
        if reason is AttentionReason.APPROVAL:
            self._enqueue_notification(target, _APPROVAL_NOTIFICATION)

    def _clear_attention(self, target: VoiceTarget) -> None:
        self._active_attention = {
            key for key in self._active_attention if key[0] != target
        }

    def _enqueue_notification(
        self, target: VoiceTarget, text: str, speech_id: str = ""
    ) -> None:
        try:
            item = SpeechItem(target=target, text=text)
            if speech_id:
                item = SpeechItem(
                    target=target, text=text, speech_id=speech_id
                )
            self.voice_gateway.enqueue_speech(item)
        except RuntimeError:
            if self.turn_timer is not None and speech_id:
                self.turn_timer.mark_voice_terminal(target, speech_id)
            self.voice_gateway.report_failure(target, "speech_queue_full")

    def observe_voice_event(self, event: object) -> None:
        if self.turn_timer is not None:
            self.turn_timer.observe_voice(event)
        previous = self._previous_event_sink
        if previous is not None:
            try:
                previous(event)
            except Exception:
                pass
        if not isinstance(event, VoiceEvent) or event.kind is not VoiceEventKind.FAILURE:
            return
        message = _VOICE_FAILURE_MESSAGES.get(event.error_code)
        if message is None:
            return
        self._voice_sequence += 1
        self.interaction_hub.publish(
            InteractionEvent(
                kind=InteractionKind.MESSAGE,
                agent_id=event.target.agent_id,
                session_id=event.target.session_id,
                project_id=event.target.project_id,
                source="voice-gateway",
                source_version="1",
                sequence=self._voice_sequence,
                correlation_id=f"voice-failure:{uuid.uuid4()}",
                payload=MessagePayload(
                    role=MessageRole.SYSTEM,
                    phase=MessagePhase.COMPLETE,
                    text=message,
                ),
            )
        )

    @staticmethod
    def _target(command: ControlCommand) -> VoiceTarget:
        return VoiceTarget(
            command.agent_id,
            command.session_id,
            command.project_id,
        )


_VOICE_FAILURE_MESSAGES = {
    "voice_input_failed": "语音输入不可用，请检查麦克风后重试。",
    "voice_no_transcript": "没有听清，请按住说话键再试一次。",
    "voice_asr_timeout": "语音识别超时，可直接输入文字。",
    "voice_prompt_failed": "语音指令暂时无法提交，请稍后重试。",
    "voice_output_failed": "回复已生成，但语音播放失败。",
    "speech_queue_full": "语音提示已跳过。",
}

_COMPLETION_NOTIFICATION = "Codex 已完成工作，请查看结果。"
_FAILURE_NOTIFICATION = "Codex 工作未完成，请查看详情。"
_APPROVAL_NOTIFICATION = "Codex 需要你的确认。"
_INPUT_NOTIFICATION = "Codex 需要你提供信息。"

_TASK_FAILURE_NOTIFICATIONS = {
    "codex_connection_lost": "Codex 控制连接中断，正在重连。",
    "codex_control_failed": "语音指令未被 Codex 接受，请查看终端。",
    "codex_control_timeout": "Codex 控制响应超时，请稍后重试。",
    "codex_thread_ambiguous": "检测到多个 Codex 会话，暂时无法语音控制。",
    "interrupted": "Codex 已停止当前工作。",
}

_VOICE_APPROVAL_PHRASES = {
    "批准": ControlKind.APPROVE,
    "确认": ControlKind.APPROVE,
    "同意": ControlKind.APPROVE,
    "允许": ControlKind.APPROVE,
    "可以": ControlKind.APPROVE,
    "拒绝": ControlKind.REJECT,
    "不同意": ControlKind.REJECT,
    "不允许": ControlKind.REJECT,
    "取消": ControlKind.REJECT,
}
