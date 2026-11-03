"""The voice host's decisions between the Voice Gateway and the Bridge.

Press-to-talk targets the session the Bridge has in focus. A transcript
becomes a prompt for that session, except two fixed phrase sets (ADR 0043
D3): an approval phrase decides the session's single answerable approval and
never becomes a prompt, and ``取消`` discards the utterance. Spoken notices
come from console transitions, so the voice tier never sees content.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol
import uuid

from deskhelm_client import CommandResult, ConsoleChange, ConsoleState, result_text
from deskhelm_protocol import SessionKey

try:
    from deskhelm_voice import SpeechItem, Transcript, VoiceEvent, VoiceEventKind, VoiceGateway, VoiceTarget
except ModuleNotFoundError as error:
    if error.name != "deskhelm_voice":
        raise
    from voice.deskhelm_voice import SpeechItem, Transcript, VoiceEvent, VoiceEventKind, VoiceGateway, VoiceTarget

APPROVAL_PHRASES = {
    "批准": "approve",
    "确认": "approve",
    "同意": "approve",
    "允许": "approve",
    "可以": "approve",
    "拒绝": "reject",
    "不同意": "reject",
    "不允许": "reject",
}
# Withdraws the utterance; it never rejects an approval.
DISCARD_PHRASES = frozenset({"取消"})

LINES = {
    "no_focus": "没有聚焦的会话，请先在页面或面板上选择一个会话。",
    "discarded": "已取消这句话，没有发送。",
    "no_pending_approval": "没有等待确认的请求，这句话没有发送。",
    "approval_ambiguous": "有多个请求等待确认，请在屏幕上逐个处理。",
}
# Gateway failures worth a spoken line; output failures cannot be spoken.
FAILURE_LINES = {
    "voice_input_failed": "语音输入不可用，请检查麦克风后重试。",
    "voice_no_transcript": "没有听清，请按住说话键再试一次。",
    "voice_asr_timeout": "语音识别超时，可直接输入文字。",
    "voice_prompt_failed": "语音指令暂时无法提交，请稍后重试。",
}
NOTICES = {
    "approval": "{agent} 需要你的确认。",
    "input": "{agent} 需要你提供信息。",
    "completed": "{agent} 已完成工作，请查看结果。",
    "interrupted": "{agent} 已停止当前工作。",
    "failed": "{agent} 工作未完成，请查看详情。",
    "usage_limit": "{agent} 的用量已到上限。",
}
AGENT_LABELS = {"codex": "Codex", "claude": "Claude", "grok": "Grok"}
MAX_PROJECT_NAME_CHARS = 64
# Lines about the host itself, not one session.
HOST_TARGET = VoiceTarget("deskhelm", "voice-host", "deskhelm")


class _Client(Protocol):
    def console(self) -> ConsoleState: ...

    def command(
        self,
        kind: str,
        session: SessionKey,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> CommandResult: ...


class VoiceHost:
    def __init__(self, client: _Client, gateway: VoiceGateway) -> None:
        self.client = client
        self.gateway = gateway
        self._capture: tuple[VoiceTarget, str] | None = None
        self._previous_sink: Callable[[object], None] | None = None
        self._unregister: Callable[[], None] | None = None

    def start(self) -> None:
        self._previous_sink = self.gateway.event_sink
        self.gateway.event_sink = self._voice_event
        self._unregister = self.gateway.register_prompt_sink(self._transcript)

    def close(self) -> None:
        if self._unregister is not None:
            self._unregister()
            self._unregister = None
        if self.gateway.event_sink == self._voice_event:
            self.gateway.event_sink = self._previous_sink

    # Press to talk -----------------------------------------------------

    def press(self) -> bool:
        state = self.client.console()
        view = state.session(state.focus) if state.focus is not None else None
        if state.focus is None or view is None or view.get("lifecycle") != "active":
            self.say(HOST_TARGET, LINES["no_focus"])
            return False
        target = VoiceTarget(state.focus.agent_id, state.focus.session_id, state.focus.project_id)
        activation = uuid.uuid4().hex
        try:
            self.gateway.press_ptt(target, activation_id=activation)
        except RuntimeError:
            return False
        self._capture = (target, activation)
        return True

    def release(self) -> bool:
        capture, self._capture = self._capture, None
        if capture is None:
            return False
        # The release names the press it ends, so a focus change in between
        # cannot redirect the utterance.
        return self.gateway.release_ptt(capture[0], activation_id=capture[1])

    # Gateway callbacks -------------------------------------------------

    def _transcript(self, target: VoiceTarget, transcript: Transcript) -> None:
        key = SessionKey(target.agent_id, target.session_id, target.project_id)
        phrase = transcript.normalized_text.strip()
        if phrase in DISCARD_PHRASES:
            self.say(target, LINES["discarded"])
            return
        decision = APPROVAL_PHRASES.get(phrase)
        if decision is not None:
            tickets = self.client.console().approvals(key)
            if not tickets:
                self.say(target, LINES["no_pending_approval"])
                return
            if len(tickets) > 1:
                self.say(target, LINES["approval_ambiguous"])
                return
            result = self.client.command(
                decision,
                key,
                {"approval_id": tickets[0]["approval_id"]},
                idempotency_key=f"voice-approval:{transcript.transcript_id}",
            )
        else:
            result = self.client.command(
                "submit_prompt",
                key,
                {"text": transcript.normalized_text},
                idempotency_key=f"voice-transcript:{transcript.transcript_id}",
            )
        if not result.accepted:
            self.say(target, result_text(result.code))

    def _voice_event(self, event: object) -> None:
        previous = self._previous_sink
        if previous is not None:
            try:
                previous(event)
            except Exception:
                pass
        if isinstance(event, VoiceEvent) and event.kind is VoiceEventKind.FAILURE:
            line = FAILURE_LINES.get(event.error_code)
            if line is not None:
                self.say(event.target, line)

    # Notices -----------------------------------------------------------

    def on_console(self, change: ConsoleChange) -> None:
        if change.reset:
            # A snapshot is the baseline; only later transitions are spoken.
            return
        for key, view in change.state.sessions.items():
            before = change.previous.sessions.get(key)
            if before is None or before is view:
                continue
            agent = AGENT_LABELS.get(key.agent_id, "Agent")
            target = VoiceTarget(key.agent_id, key.session_id, key.project_id)
            for notice in _notices(before, view):
                self.say(target, _with_project(key.project_id, NOTICES[notice].format(agent=agent)))

    def say(self, target: VoiceTarget, text: str) -> None:
        try:
            self.gateway.enqueue_speech(SpeechItem(target=target, text=text))
        except RuntimeError:
            # A full speech queue skips the line rather than blocking.
            pass


def _notices(before: dict[str, Any], view: dict[str, Any]) -> list[str]:
    notices: list[str] = []
    outcome = view.get("last_outcome")
    if outcome and outcome != before.get("last_outcome"):
        status = outcome.get("status")
        if status in ("completed", "interrupted"):
            notices.append(status)
        else:
            notices.append("usage_limit" if outcome.get("failure") == "usage_limit" else "failed")
    activity, previous = view.get("activity"), before.get("activity")
    if activity == "waiting_approval" and previous != "waiting_approval":
        notices.append("approval")
    elif activity == "waiting_input" and previous != "waiting_input":
        notices.append("input")
    return notices


def _with_project(project_id: str, text: str) -> str:
    """Keep the project label short and plain for the fixed speech path."""
    name = "".join(character for character in " ".join(project_id.split()) if character.isalnum() or character in " ._-")
    name = name.strip(" ._-")[:MAX_PROJECT_NAME_CHARS] or "当前项目"
    return f"项目 {name}，{text}"
