from pathlib import Path
from queue import Queue
import time
import unittest

from voice.deskhelm_voice import (
    CapturedAudio,
    FakeAsrProvider,
    FakeCaptureProvider,
    FakePlaybackProvider,
    FakeStreamingCaptureProvider,
    FakeTtsProvider,
    PcmChunk,
    PcmStreamFormat,
    SpeechItem,
    Transcript,
    VoiceEvent,
    VoiceEventKind,
    VoiceGateway,
    VoiceNoTranscript,
    VoicePttState,
    VoiceTarget,
)


ROOT = Path(__file__).resolve().parents[1]


class VoiceGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = VoiceTarget("codex", "session-42", "deskhelm")
        self.gateways: list[VoiceGateway] = []

    def tearDown(self) -> None:
        for gateway in self.gateways:
            gateway.close()

    def test_ptt_preserves_raw_and_normalized_transcript(self) -> None:
        transcript = Transcript(
            raw_text="嗯 请 运行测试",
            normalized_text="请运行测试",
            transcript_id="transcript-1",
        )
        capture = FakeCaptureProvider([self._audio()])
        asr = FakeAsrProvider([transcript])
        prompts: Queue[tuple[VoiceTarget, Transcript]] = Queue()
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(capture, asr, event_sink=events.put)
        gateway.register_prompt_sink(lambda target, text: prompts.put((target, text)))

        gateway.press_ptt(self.target)
        self.assertTrue(capture.started.wait(timeout=1))
        self.assertEqual(gateway.ptt_state(), VoicePttState.CAPTURING)
        gateway.release_ptt()
        target, submitted = prompts.get(timeout=1)
        self._wait_for_state(gateway, VoicePttState.IDLE)

        self.assertEqual(target, self.target)
        self.assertEqual(submitted.raw_text, "嗯 请 运行测试")
        self.assertEqual(submitted.normalized_text, "请运行测试")
        event_kinds = self._drain_event_kinds(events)
        self.assertEqual(event_kinds[0], VoiceEventKind.PTT_STARTED)
        self.assertIn(VoiceEventKind.TRANSCRIPT_READY, event_kinds)

    def test_capture_failure_returns_to_idle_with_recoverable_event(self) -> None:
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            FakeCaptureProvider([]),
            FakeAsrProvider([]),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        self._wait_for_state(gateway, VoicePttState.IDLE)
        gateway.release_ptt()

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        self.assertEqual(emitted[0].kind, VoiceEventKind.PTT_STARTED)
        self.assertEqual(emitted[-1].kind, VoiceEventKind.FAILURE)
        self.assertEqual(emitted[-1].error_code, "voice_input_failed")

    def test_empty_asr_result_has_distinct_safe_failure(self) -> None:
        class NoTranscriptAsr:
            def transcribe(self, _audio, _cancel):
                raise VoiceNoTranscript()

        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            FakeCaptureProvider([self._audio()]),
            NoTranscriptAsr(),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        gateway.release_ptt()
        self._wait_for_state(gateway, VoicePttState.IDLE)

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        self.assertEqual(emitted[-1].error_code, "voice_no_transcript")

    def test_streaming_capture_is_assembled_only_after_release(self) -> None:
        format = PcmStreamFormat(16000)
        capture = FakeStreamingCaptureProvider(
            [
                PcmChunk(b"\x01\x00" * 160, format, 0),
                PcmChunk(b"\x02\x00" * 160, format, 160),
            ]
        )
        asr = FakeAsrProvider([Transcript("raw", "normalized")])
        prompts: Queue[tuple[VoiceTarget, Transcript]] = Queue()
        gateway = self._gateway(capture, asr)
        gateway.register_prompt_sink(lambda target, text: prompts.put((target, text)))

        gateway.press_ptt(self.target)
        self.assertTrue(capture.started.wait(timeout=1))
        self._wait_for(lambda: capture.chunks_read == 2)
        self.assertEqual(gateway.ptt_state(), VoicePttState.CAPTURING)
        self.assertEqual(asr.requests, [])

        gateway.release_ptt()
        prompts.get(timeout=1)
        self._wait_for_state(gateway, VoicePttState.IDLE)

        self.assertEqual(capture.streams_opened, 1)
        self.assertEqual(capture.streams_closed, 1)
        self.assertEqual(
            asr.requests[0].data,
            b"\x01\x00" * 160 + b"\x02\x00" * 160,
        )

    def test_streaming_capture_rejects_discontinuity_and_closes_stream(self) -> None:
        format = PcmStreamFormat(16000)
        capture = FakeStreamingCaptureProvider(
            [
                PcmChunk(b"\x01\x00", format, 0),
                PcmChunk(b"\x02\x00", format, 2),
            ]
        )
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            capture,
            FakeAsrProvider([Transcript("raw", "normalized")]),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        self._wait_for_state(gateway, VoicePttState.IDLE)

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        self.assertEqual(emitted[-1].kind, VoiceEventKind.FAILURE)
        self.assertEqual(emitted[-1].error_code, "voice_input_failed")
        self.assertEqual(capture.streams_closed, 1)

    def test_streaming_capture_enforces_gateway_byte_limit(self) -> None:
        format = PcmStreamFormat(16000)
        capture = FakeStreamingCaptureProvider(
            [PcmChunk(b"\x01\x00\x02\x00", format, 0)]
        )
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            capture,
            FakeAsrProvider([Transcript("raw", "normalized")]),
            event_sink=events.put,
            max_capture_bytes=2,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        self._wait_for_state(gateway, VoicePttState.IDLE)

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        self.assertEqual(emitted[-1].kind, VoiceEventKind.FAILURE)
        self.assertEqual(capture.streams_closed, 1)

    def test_streaming_capture_rejects_end_before_ptt_release(self) -> None:
        format = PcmStreamFormat(16000)
        capture = FakeStreamingCaptureProvider(
            [PcmChunk(b"\x01\x00", format, 0)],
            end_on_exhaustion=True,
        )
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            capture,
            FakeAsrProvider([Transcript("raw", "normalized")]),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        self._wait_for_state(gateway, VoicePttState.IDLE)

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        self.assertEqual(emitted[-1].error_code, "voice_input_failed")
        self.assertEqual(capture.streams_closed, 1)

    def test_targeted_release_rejects_other_session_and_stale_activation(self) -> None:
        capture = FakeCaptureProvider([self._audio()])
        gateway = self._gateway(
            capture,
            FakeAsrProvider([Transcript("raw", "normalized")]),
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)
        other_target = VoiceTarget("codex", "session-other", "deskhelm")

        gateway.press_ptt(self.target, activation_id="press-current")
        self.assertTrue(capture.started.wait(timeout=1))

        self.assertFalse(
            gateway.release_ptt(other_target, activation_id="press-current")
        )
        self.assertFalse(
            gateway.release_ptt(self.target, activation_id="press-stale")
        )
        self.assertEqual(gateway.ptt_state(), VoicePttState.CAPTURING)
        self.assertTrue(
            gateway.release_ptt(self.target, activation_id="press-current")
        )
        self._wait_for_state(gateway, VoicePttState.IDLE)

        with self.assertRaisesRegex(ValueError, "both be set"):
            gateway.release_ptt(self.target)

    def test_new_ptt_cancels_current_interruptible_playback(self) -> None:
        capture = FakeCaptureProvider([self._audio()])
        asr = FakeAsrProvider([Transcript("raw", "normalized")])
        playback = FakePlaybackProvider(block_until_cancel=True)
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            capture,
            asr,
            playback=playback,
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)
        gateway.enqueue_speech(
            SpeechItem(
                target=self.target,
                text="interruptible response",
                speech_id="speech-1",
            )
        )
        self.assertTrue(playback.started.wait(timeout=1))

        gateway.press_ptt(self.target)
        self.assertTrue(capture.started.wait(timeout=1))
        self._wait_for(lambda: playback.cancelled_count == 1)
        gateway.release_ptt()
        self._wait_for_state(gateway, VoicePttState.IDLE)

        event_kinds = self._drain_event_kinds(events)
        self.assertIn(VoiceEventKind.SPEECH_CANCELLED, event_kinds)

    def test_speech_queue_is_bounded_while_playback_is_busy(self) -> None:
        playback = FakePlaybackProvider(block_until_cancel=True)
        gateway = self._gateway(
            FakeCaptureProvider([self._audio()]),
            FakeAsrProvider([Transcript("raw", "normalized")]),
            playback=playback,
            max_speech_items=1,
        )
        gateway.enqueue_speech(SpeechItem(self.target, "current", speech_id="current"))
        self.assertTrue(playback.started.wait(timeout=1))
        gateway.enqueue_speech(SpeechItem(self.target, "queued", speech_id="queued"))

        with self.assertRaisesRegex(RuntimeError, "capacity"):
            gateway.enqueue_speech(
                SpeechItem(self.target, "overflow", speech_id="overflow")
            )

        self.assertEqual(gateway.queued_speech_count(), 1)
        gateway.stop_speaking(self.target)

    def _gateway(
        self,
        capture: FakeCaptureProvider | FakeStreamingCaptureProvider,
        asr,
        *,
        playback: FakePlaybackProvider | None = None,
        event_sink=None,
        max_capture_bytes: int = 1 << 20,
        max_speech_items: int = 8,
    ) -> VoiceGateway:
        gateway = VoiceGateway(
            capture_provider=capture,
            asr_provider=asr,
            tts_provider=FakeTtsProvider(),
            playback_provider=playback or FakePlaybackProvider(),
            max_capture_bytes=max_capture_bytes,
            max_speech_items=max_speech_items,
            event_sink=event_sink,
        )
        self.gateways.append(gateway)
        return gateway

    @staticmethod
    def _audio() -> CapturedAudio:
        return CapturedAudio(b"fake-pcm", sample_rate_hz=16000)

    @staticmethod
    def _wait_for_state(gateway: VoiceGateway, state: VoicePttState) -> None:
        VoiceGatewayTests._wait_for(lambda: gateway.ptt_state() is state)

    @staticmethod
    def _wait_for(predicate) -> None:
        deadline = time.monotonic() + 1
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not predicate():
            raise AssertionError("condition was not reached")

    @staticmethod
    def _drain_event_kinds(events: Queue[VoiceEvent]) -> list[VoiceEventKind]:
        values = []
        while not events.empty():
            values.append(events.get_nowait().kind)
        return values


if __name__ == "__main__":
    unittest.main()
