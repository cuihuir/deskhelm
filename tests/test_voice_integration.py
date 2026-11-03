from dataclasses import replace
import json
from pathlib import Path
from queue import Queue
import time
import unittest

from deskhelm_bridge.agent_gateway import (
    AgentGateway,
    AgentProviderEvent,
    AgentRunResult,
    AgentRunStatus,
)
from deskhelm_bridge.control import (
    ControlCommand,
    ControlKind,
    ReleasePttPayload,
    StopSpeakingPayload,
)
from deskhelm_bridge.control_result import ControlResultCode
from deskhelm_bridge.control_router import ControlRouter
from deskhelm_bridge.fake_agent_provider import FakeAgentProvider, FakeRunScript
from deskhelm_bridge.interaction import (
    ApprovalRequestPayload,
    AttentionPayload,
    AttentionReason,
    InteractionEvent,
    InteractionKind,
    MessagePayload,
    MessagePhase,
    MessageRole,
    TaskPayload,
    UserInputPayload,
)
from deskhelm_bridge.interaction_subscription import InteractionHub
from deskhelm_bridge.session_registry import SessionKey, SessionRegistry
from deskhelm_bridge.turn_timing import VoiceAgentTurnTimer
from deskhelm_bridge.voice_integration import VoiceBridgeIntegration
from voice.deskhelm_voice import (
    CapturedAudio,
    FakeAsrProvider,
    FakeCaptureProvider,
    FakePlaybackProvider,
    FakeTtsProvider,
    Transcript,
    VoiceEvent,
    VoiceGateway,
    VoicePttState,
    VoiceTarget,
)


ROOT = Path(__file__).resolve().parents[1]
CONTROL_FIXTURES = ROOT / "tests" / "fixtures" / "protocol" / "control-v1"


class VoiceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = SessionKey("codex", "session-42", "deskhelm")
        self.target = VoiceTarget("codex", "session-42", "deskhelm")
        self.registry = SessionRegistry(slot_count=2)
        self.registry.register(self.session, preferred_slot=0, now_ms=1786935000000)
        self.registry.focus(self.session)
        self.router = ControlRouter(self.registry)
        self.hub = InteractionHub()
        self.resources = []

    def tearDown(self) -> None:
        for resource in reversed(self.resources):
            resource.close()

    def test_fake_pipeline_completes_ptt_agent_and_speech_flow(self) -> None:
        capture = FakeCaptureProvider(
            [CapturedAudio(b"fake-pcm", sample_rate_hz=16000)]
        )
        transcript = Transcript(
            raw_text="请 帮我 总结",
            normalized_text="请帮我总结",
            transcript_id="voice-transcript-1",
        )
        asr = FakeAsrProvider([transcript])
        tts = FakeTtsProvider()
        playback = FakePlaybackProvider()
        voice = VoiceGateway(capture, asr, tts, playback)
        self.resources.append(voice)
        agent = FakeAgentProvider(
            [
                FakeRunScript(
                    events=(
                        AgentProviderEvent(
                            kind=InteractionKind.MESSAGE,
                            correlation_id="agent-message-1",
                            payload=MessagePayload(
                                role=MessageRole.ASSISTANT,
                                phase=MessagePhase.COMPLETE,
                                text="这是总结结果",
                            ),
                        ),
                    ),
                    result=AgentRunResult(AgentRunStatus.COMPLETED),
                )
            ]
        )
        agent_gateway = AgentGateway(agent, self.hub.publish, ROOT)
        agent_gateway.register_handlers(self.router)
        self.resources.append(agent_gateway)
        timings = []
        integration = VoiceBridgeIntegration(
            voice,
            self.router,
            self.hub,
            turn_timer=VoiceAgentTurnTimer(timings.append),
        )
        integration.register()
        self.resources.append(integration)

        voice.press_ptt(self.target)
        self.assertTrue(capture.started.wait(timeout=1))
        voice.release_ptt()
        self.assertTrue(playback.completed.wait(timeout=1))
        self._wait_for(lambda: len(timings) == 1)

        self.assertEqual(agent.requests[0].prompt, "请帮我总结")
        self.assertEqual(transcript.raw_text, "请 帮我 总结")
        self.assertEqual(
            tts.requests,
            ["项目 deskhelm，Codex 已完成工作，请查看结果。"],
        )
        self.assertGreater(len(playback.requests[0].data), 0)
        self.assertEqual(playback.requests[0].sample_rate_hz, 24000)
        self.assertIsNotNone(timings[0].dispatch_to_notification_ready_ms)
        self.assertIsNotNone(timings[0].speech_started_to_tts_audio_ready_ms)

    def test_speak_and_stop_controls_route_to_owned_playback(self) -> None:
        playback = FakePlaybackProvider(block_until_cancel=True)
        voice = VoiceGateway(
            FakeCaptureProvider(
                [CapturedAudio(b"fake-pcm", sample_rate_hz=16000)]
            ),
            FakeAsrProvider([Transcript("raw", "normalized")]),
            FakeTtsProvider(),
            playback,
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)
        speak = self._command("speak.json")

        speak_result = self.router.route(speak, now_ms=speak.issued_at)
        self.assertTrue(playback.started.wait(timeout=1))
        stop = self._command("stop-speaking.json")
        stop = replace(
            stop,
            payload=StopSpeakingPayload(speech_id=speak.command_id),
        )
        stop_result = self.router.route(stop, now_ms=stop.issued_at)
        self._wait_for(lambda: playback.cancelled_count == 1)

        self.assertEqual(speak_result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(stop_result.code, ControlResultCode.DISPATCHED)

    def test_external_ptt_controls_require_matching_target_and_press(self) -> None:
        capture = FakeCaptureProvider(
            [CapturedAudio(b"fake-pcm", sample_rate_hz=16000)]
        )
        voice = VoiceGateway(
            capture,
            FakeAsrProvider([Transcript("raw", "normalized")]),
            FakeTtsProvider(),
            FakePlaybackProvider(),
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)
        press = self._command("press-ptt.json")

        press_result = self.router.route(press, now_ms=press.issued_at)
        self.assertTrue(capture.started.wait(timeout=1))
        stale_release = replace(
            self._command("release-ptt.json"),
            command_id="command-release-ptt-stale",
            idempotency_key="release-ptt-stale",
            payload=ReleasePttPayload(press_command_id="command-press-old"),
        )
        stale_result = self.router.route(
            stale_release, now_ms=stale_release.issued_at
        )
        self.assertIs(voice.ptt_state(), VoicePttState.CAPTURING)
        release = self._command("release-ptt.json")
        release_result = self.router.route(release, now_ms=release.issued_at)
        self._wait_for(lambda: voice.ptt_state() is VoicePttState.IDLE)

        self.assertEqual(press.kind, ControlKind.PRESS_PTT)
        self.assertEqual(press_result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(stale_result.code, ControlResultCode.DISPATCH_FAILED)
        self.assertEqual(release_result.code, ControlResultCode.DISPATCHED)

    def test_ptt_does_not_start_without_a_focused_session(self) -> None:
        capture = FakeCaptureProvider(
            [CapturedAudio(b"fake-pcm", sample_rate_hz=16000)]
        )
        voice = VoiceGateway(
            capture,
            FakeAsrProvider([Transcript("raw", "normalized")]),
            FakeTtsProvider(),
            FakePlaybackProvider(),
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)
        self.registry.clear_focus()

        result = self.router.route(
            self._command("press-ptt.json"), now_ms=1786935107000
        )

        self.assertEqual(result.code, ControlResultCode.TARGET_NOT_FOCUSED)
        self.assertFalse(capture.started.wait(timeout=0.05))

    def test_full_speech_queue_does_not_break_interaction_publishers(self) -> None:
        events: Queue[VoiceEvent] = Queue()
        playback = FakePlaybackProvider(block_until_cancel=True)
        voice = VoiceGateway(
            FakeCaptureProvider(
                [CapturedAudio(b"fake-pcm", sample_rate_hz=16000)]
            ),
            FakeAsrProvider([Transcript("raw", "normalized")]),
            FakeTtsProvider(),
            playback,
            max_speech_items=1,
            event_sink=events.put,
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)
        for index in range(3):
            self.hub.publish(
                self._terminal_event(InteractionKind.TASK_COMPLETED, index)
            )
            if index == 0:
                self.assertTrue(playback.started.wait(timeout=1))

        self._wait_for(
            lambda: any(
                event.error_code == "speech_queue_full"
                for event in list(events.queue)
            )
        )
        voice.stop_speaking(self.target)

    def test_assistant_messages_remain_silent_and_completion_is_fixed(self) -> None:
        tts = FakeTtsProvider()
        playback = FakePlaybackProvider()
        voice = VoiceGateway(
            FakeCaptureProvider([]), FakeAsrProvider([]), tts, playback
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)

        for sequence, text in enumerate(("intermediate", "final")):
            message = AgentProviderEvent(
                kind=InteractionKind.MESSAGE,
                correlation_id=f"message-{sequence}",
                payload=MessagePayload(
                    role=MessageRole.ASSISTANT,
                    phase=MessagePhase.COMPLETE,
                    text=text,
                ),
            )
            self.hub.publish(self._interaction_from_provider(message, sequence))

        self.assertEqual(tts.requests, [])
        self.hub.publish(
            self._terminal_event(InteractionKind.TASK_COMPLETED, sequence=2)
        )
        self.assertTrue(playback.completed.wait(timeout=1))
        self.assertEqual(
            tts.requests,
            ["项目 deskhelm，Codex 已完成工作，请查看结果。"],
        )

    def test_human_attention_events_use_project_labelled_notifications(self) -> None:
        tts = FakeTtsProvider()
        voice = VoiceGateway(
            FakeCaptureProvider([]),
            FakeAsrProvider([]),
            tts,
            FakePlaybackProvider(),
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)

        self.hub.publish(
            InteractionEvent(
                kind=InteractionKind.APPROVAL_REQUEST,
                agent_id="codex",
                session_id="session-42",
                project_id="deskhelm",
                source="fake-agent",
                source_version="1",
                sequence=1,
                correlation_id="approval-1",
                occurred_at=1_000,
                payload=ApprovalRequestPayload(
                    request_id="approval-1",
                    summary="private approval summary",
                    expires_at=2_000,
                ),
            )
        )
        self.hub.publish(
            InteractionEvent(
                kind=InteractionKind.USER_INPUT_REQUIRED,
                agent_id="codex",
                session_id="session-42",
                project_id="deskhelm",
                source="fake-agent",
                source_version="1",
                sequence=2,
                correlation_id="input-1",
                payload=UserInputPayload(prompt="private input prompt"),
            )
        )
        self.hub.publish(self._terminal_event(InteractionKind.TASK_FAILED, 3))

        self._wait_for(lambda: len(tts.requests) == 3)
        self.assertEqual(
            tts.requests,
            [
                "项目 deskhelm，Codex 需要你的确认。",
                "项目 deskhelm，Codex 需要你提供信息。",
                "项目 deskhelm，Codex 工作未完成，请查看详情。",
            ],
        )

    def test_status_attention_is_deduplicated_and_non_actionable(self) -> None:
        tts = FakeTtsProvider()
        voice = VoiceGateway(
            FakeCaptureProvider([]),
            FakeAsrProvider([]),
            tts,
            FakePlaybackProvider(),
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)

        def publish(active: bool, sequence: int) -> None:
            self.hub.publish(
                InteractionEvent(
                    kind=InteractionKind.ATTENTION,
                    agent_id=self.session.agent_id,
                    session_id=self.session.session_id,
                    project_id=self.session.project_id,
                    source="codex-app-server",
                    source_version="test",
                    sequence=sequence,
                    payload=AttentionPayload(
                        reason=AttentionReason.APPROVAL,
                        active=active,
                    ),
                )
            )

        publish(True, 1)
        publish(True, 2)
        self._wait_for(lambda: len(tts.requests) == 1)
        self.assertEqual(self.router.pending_approval_count(), 0)

        now_ms = int(time.time() * 1000)
        self.hub.publish(
            InteractionEvent(
                kind=InteractionKind.APPROVAL_REQUEST,
                agent_id=self.session.agent_id,
                session_id=self.session.session_id,
                project_id=self.session.project_id,
                source="codex-app-server",
                source_version="test",
                sequence=3,
                occurred_at=now_ms,
                correlation_id="approval-after-status",
                payload=ApprovalRequestPayload(
                    request_id="approval-after-status",
                    summary="fixed summary",
                    expires_at=now_ms + 10_000,
                ),
            )
        )
        time.sleep(0.05)
        self.assertEqual(
            tts.requests,
            ["项目 deskhelm，Codex 需要你的确认。"],
        )

        publish(False, 4)
        publish(True, 5)
        self._wait_for(lambda: len(tts.requests) == 2)
        self.assertEqual(
            tts.requests,
            [
                "项目 deskhelm，Codex 需要你的确认。",
                "项目 deskhelm，Codex 需要你的确认。",
            ],
        )

    def test_control_failure_uses_actionable_notification(self) -> None:
        tts = FakeTtsProvider()
        voice = VoiceGateway(
            FakeCaptureProvider([]),
            FakeAsrProvider([]),
            tts,
            FakePlaybackProvider(),
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)
        self.hub.publish(
            InteractionEvent(
                kind=InteractionKind.TASK_FAILED,
                agent_id=self.session.agent_id,
                session_id=self.session.session_id,
                project_id=self.session.project_id,
                source="codex-app-server",
                source_version="test",
                sequence=1,
                correlation_id="voice-control-failure",
                payload=TaskPayload(
                    message="private failure",
                    error_code="codex_control_failed",
                ),
            )
        )

        self._wait_for(lambda: len(tts.requests) == 1)
        self.assertEqual(
            tts.requests,
            ["项目 deskhelm，语音指令未被 Codex 接受，请查看终端。"],
        )

    def test_notification_uses_target_project_without_private_payload(self) -> None:
        tts = FakeTtsProvider()
        voice = VoiceGateway(
            FakeCaptureProvider([]),
            FakeAsrProvider([]),
            tts,
            FakePlaybackProvider(),
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)

        self.hub.publish(
            InteractionEvent(
                kind=InteractionKind.TASK_FAILED,
                agent_id="codex",
                session_id="client-session",
                project_id="client-portal",
                source="fake-agent",
                source_version="1",
                sequence=1,
                correlation_id="client-failure",
                payload=TaskPayload(
                    message="private failure detail",
                    error_code="failed",
                ),
            )
        )

        self._wait_for(lambda: len(tts.requests) == 1)
        self.assertEqual(
            tts.requests,
            ["项目 client-portal，Codex 工作未完成，请查看详情。"],
        )
        self.assertNotIn("private failure detail", tts.requests[0])

    def test_voice_failure_publishes_friendly_system_message(self) -> None:
        received = []
        self.hub.subscribe(received.append)
        voice = VoiceGateway(
            FakeCaptureProvider([]), FakeAsrProvider([]), FakeTtsProvider(),
            FakePlaybackProvider(),
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)

        voice.report_failure(self.target, "voice_no_transcript")

        self.assertEqual(len(received), 1)
        event = received[0]
        self.assertIs(event.payload.role, MessageRole.SYSTEM)
        self.assertEqual(event.payload.text, "没有听清，请按住说话键再试一次。")
        self.assertEqual(event.source, "voice-gateway")

        voice.report_failure(self.target, "voice_prompt_failed")
        self.assertEqual(len(received), 2)
        self.assertEqual(
            received[-1].payload.text,
            "语音指令暂时无法提交，请稍后重试。",
        )

    def test_confirmation_voice_phrase_decides_single_pending_approval(self) -> None:
        voice = VoiceGateway(
            FakeCaptureProvider([]),
            FakeAsrProvider([]),
            FakeTtsProvider(),
            FakePlaybackProvider(),
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)
        decisions = []
        unregister = self.router.register_handler(
            ControlKind.APPROVE,
            decisions.append,
        )
        self.resources.append(_CallbackResource(unregister))
        now_ms = int(time.time() * 1000)
        approval = InteractionEvent(
            kind=InteractionKind.APPROVAL_REQUEST,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            source="codex-app-server",
            source_version="test",
            sequence=1,
            occurred_at=now_ms,
            correlation_id="approval-voice",
            payload=ApprovalRequestPayload(
                request_id="approval-voice",
                summary="fixed summary",
                expires_at=now_ms + 10_000,
            ),
        )
        self.router.observe_interaction(approval)

        integration.submit_transcript(
            self.target,
            Transcript("确认", "确认", transcript_id="approval-transcript"),
        )

        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertIs(decision.kind, ControlKind.APPROVE)
        self.assertEqual(decision.payload.request_id, "approval-voice")
        self.assertEqual(decision.payload.summary, "fixed summary")
        self.assertEqual(decision.expires_at, now_ms + 10_000)
        self.assertEqual(self.router.pending_approval_count(), 0)

    def test_approval_word_is_prompt_when_no_approval_is_pending(self) -> None:
        voice = VoiceGateway(
            FakeCaptureProvider([]),
            FakeAsrProvider([]),
            FakeTtsProvider(),
            FakePlaybackProvider(),
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)
        prompts = []
        unregister = self.router.register_handler(
            ControlKind.SUBMIT_PROMPT,
            prompts.append,
        )
        self.resources.append(_CallbackResource(unregister))

        integration.submit_transcript(
            self.target,
            Transcript("批准", "批准", transcript_id="ordinary-approval-word"),
        )

        self.assertEqual(len(prompts), 1)
        self.assertIs(prompts[0].kind, ControlKind.SUBMIT_PROMPT)
        self.assertEqual(prompts[0].payload.text, "批准")

    def test_voice_refuses_ambiguous_multiple_approvals(self) -> None:
        voice = VoiceGateway(
            FakeCaptureProvider([]),
            FakeAsrProvider([]),
            FakeTtsProvider(),
            FakePlaybackProvider(),
        )
        self.resources.append(voice)
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)
        now_ms = int(time.time() * 1000)
        for sequence in (1, 2):
            request_id = f"approval-{sequence}"
            self.router.observe_interaction(
                InteractionEvent(
                    kind=InteractionKind.APPROVAL_REQUEST,
                    agent_id=self.session.agent_id,
                    session_id=self.session.session_id,
                    project_id=self.session.project_id,
                    source="codex-app-server",
                    source_version="test",
                    sequence=sequence,
                    occurred_at=now_ms,
                    correlation_id=request_id,
                    payload=ApprovalRequestPayload(
                        request_id=request_id,
                        summary="fixed summary",
                        expires_at=now_ms + 10_000,
                    ),
                )
            )

        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            integration.submit_transcript(
                self.target,
                Transcript("批准", "批准", transcript_id="ambiguous-approval"),
            )

    @staticmethod
    def _interaction_from_provider(
        provider_event: AgentProviderEvent, sequence: int
    ):
        return InteractionEvent(
            kind=provider_event.kind,
            agent_id="codex",
            session_id="session-42",
            project_id="deskhelm",
            source="fake-agent",
            source_version="1",
            sequence=sequence,
            correlation_id=provider_event.correlation_id,
            payload=provider_event.payload,
        )

    @staticmethod
    def _terminal_event(kind: InteractionKind, sequence: int) -> InteractionEvent:
        if kind is InteractionKind.TASK_COMPLETED:
            payload = TaskPayload()
        else:
            payload = TaskPayload(message="private failure", error_code="failed")
        return InteractionEvent(
            kind=kind,
            agent_id="codex",
            session_id="session-42",
            project_id="deskhelm",
            source="fake-agent",
            source_version="1",
            sequence=sequence,
            correlation_id=f"turn-{sequence}",
            payload=payload,
        )

    @staticmethod
    def _command(name: str) -> ControlCommand:
        return ControlCommand.from_dict(
            json.loads((CONTROL_FIXTURES / name).read_text(encoding="utf-8"))
        )

    @staticmethod
    def _wait_for(predicate) -> None:
        deadline = time.monotonic() + 1
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not predicate():
            raise AssertionError("condition was not reached")


class _CallbackResource:
    def __init__(self, callback) -> None:
        self.callback = callback

    def close(self) -> None:
        self.callback()


if __name__ == "__main__":
    unittest.main()
