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
from deskhelm_bridge.control import ControlCommand, StopSpeakingPayload
from deskhelm_bridge.control_result import ControlResultCode
from deskhelm_bridge.control_router import ControlRouter
from deskhelm_bridge.fake_agent_provider import FakeAgentProvider, FakeRunScript
from deskhelm_bridge.interaction import (
    InteractionKind,
    MessagePayload,
    MessagePhase,
    MessageRole,
)
from deskhelm_bridge.interaction_subscription import InteractionHub
from deskhelm_bridge.session_registry import SessionKey, SessionRegistry
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
        integration = VoiceBridgeIntegration(voice, self.router, self.hub)
        integration.register()
        self.resources.append(integration)

        voice.press_ptt(self.target)
        self.assertTrue(capture.started.wait(timeout=1))
        voice.release_ptt()
        self.assertTrue(playback.completed.wait(timeout=1))

        self.assertEqual(agent.requests[0].prompt, "请帮我总结")
        self.assertEqual(transcript.raw_text, "请 帮我 总结")
        self.assertEqual(tts.requests, ["这是总结结果"])
        self.assertEqual(playback.requests[0].data.decode("utf-8"), "这是总结结果")

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
        message = AgentProviderEvent(
            kind=InteractionKind.MESSAGE,
            correlation_id="message-capacity",
            payload=MessagePayload(
                role=MessageRole.ASSISTANT,
                phase=MessagePhase.COMPLETE,
                text="message",
            ),
        )

        for index in range(3):
            event = self._interaction_from_provider(message, index)
            self.hub.publish(event)
            if index == 0:
                self.assertTrue(playback.started.wait(timeout=1))

        self._wait_for(
            lambda: any(
                event.error_code == "speech_queue_full"
                for event in list(events.queue)
            )
        )
        voice.stop_speaking(self.target)

    @staticmethod
    def _interaction_from_provider(
        provider_event: AgentProviderEvent, sequence: int
    ):
        from deskhelm_bridge.interaction import InteractionEvent

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


if __name__ == "__main__":
    unittest.main()
