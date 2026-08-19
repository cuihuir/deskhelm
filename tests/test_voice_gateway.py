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
    FakeVadProvider,
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
    VadEvent,
    VadEventKind,
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

    def test_advisory_vad_reports_activity_without_ending_ptt(self) -> None:
        format = PcmStreamFormat(16000)
        capture = FakeStreamingCaptureProvider(
            [
                PcmChunk(b"\x01\x00" * 160, format, 0),
                PcmChunk(b"\x02\x00" * 160, format, 160),
            ]
        )
        asr = FakeAsrProvider([Transcript("raw", "normalized")])
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            capture,
            asr,
            vad=FakeVadProvider(
                [
                    VadEvent(VadEventKind.SPEECH_STARTED, 80),
                    VadEvent(VadEventKind.SPEECH_ENDED, 240),
                ]
            ),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        self._wait_for(lambda: capture.chunks_read == 2)
        self.assertEqual(gateway.ptt_state(), VoicePttState.CAPTURING)
        self.assertEqual(asr.requests, [])
        gateway.release_ptt()
        self._wait_for_state(gateway, VoicePttState.IDLE)

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        activity = [
            (event.kind, event.audio_frame_index)
            for event in emitted
            if event.kind
            in {
                VoiceEventKind.INPUT_SPEECH_STARTED,
                VoiceEventKind.INPUT_SPEECH_ENDED,
            }
        ]
        self.assertEqual(
            activity,
            [
                (VoiceEventKind.INPUT_SPEECH_STARTED, 80),
                (VoiceEventKind.INPUT_SPEECH_ENDED, 240),
            ],
        )
        self.assertLess(
            [event.kind for event in emitted].index(
                VoiceEventKind.INPUT_SPEECH_ENDED
            ),
            [event.kind for event in emitted].index(VoiceEventKind.TRANSCRIBING),
        )
        self.assertEqual(len(asr.requests[0].data), 640)

    def test_vad_failure_is_non_terminal_and_preserves_full_audio(self) -> None:
        format = PcmStreamFormat(16000)
        capture = FakeStreamingCaptureProvider(
            [PcmChunk(b"\x01\x00" * 160, format, 0)]
        )
        asr = FakeAsrProvider([Transcript("raw", "normalized")])
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            capture,
            asr,
            vad=FakeVadProvider([], fail=True),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        self.assertTrue(capture.started.wait(timeout=1))
        gateway.release_ptt()
        self._wait_for_state(gateway, VoicePttState.IDLE)

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        failures = [
            event
            for event in emitted
            if event.kind is VoiceEventKind.INPUT_ACTIVITY_FAILED
        ]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].error_code, "voice_vad_failed")
        self.assertIn(VoiceEventKind.TRANSCRIPT_READY, [e.kind for e in emitted])
        self.assertEqual(asr.requests[0].data, b"\x01\x00" * 160)

    def test_vad_close_failure_is_non_terminal(self) -> None:
        class CloseFailureSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                raise RuntimeError("private close failure")

            def process(self, _chunk, _cancel):
                return ()

            def finish(self, _cancel):
                return ()


        class CloseFailureProvider:
            def open_session(self, _format):
                return CloseFailureSession()


        format = PcmStreamFormat(16000)
        capture = FakeStreamingCaptureProvider(
            [PcmChunk(b"\x01\x00" * 160, format, 0)]
        )
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            capture,
            FakeAsrProvider([Transcript("raw", "normalized")]),
            vad=CloseFailureProvider(),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        self.assertTrue(capture.started.wait(timeout=1))
        gateway.release_ptt()
        self._wait_for_state(gateway, VoicePttState.IDLE)

        kinds = self._drain_event_kinds(events)
        self.assertEqual(kinds.count(VoiceEventKind.INPUT_ACTIVITY_FAILED), 1)
        self.assertIn(VoiceEventKind.TRANSCRIPT_READY, kinds)

    def test_invalid_vad_batch_is_not_partially_emitted(self) -> None:
        class InvalidVadSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return None

            def process(self, _chunk, _cancel):
                return (
                    VadEvent(VadEventKind.SPEECH_STARTED, 20),
                    VadEvent(VadEventKind.SPEECH_STARTED, 30),
                )

            def finish(self, _cancel):
                return ()

        class InvalidVadProvider:
            def open_session(self, _format):
                return InvalidVadSession()

        format = PcmStreamFormat(16000)
        capture = FakeStreamingCaptureProvider(
            [PcmChunk(b"\x01\x00" * 160, format, 0)]
        )
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            capture,
            FakeAsrProvider([Transcript("raw", "normalized")]),
            vad=InvalidVadProvider(),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        self.assertTrue(capture.started.wait(timeout=1))
        gateway.release_ptt()
        self._wait_for_state(gateway, VoicePttState.IDLE)

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        self.assertNotIn(
            VoiceEventKind.INPUT_SPEECH_STARTED,
            [event.kind for event in emitted],
        )
        self.assertEqual(
            sum(
                event.kind is VoiceEventKind.INPUT_ACTIVITY_FAILED
                for event in emitted
            ),
            1,
        )

    def test_excessive_vad_output_is_rejected_before_publication(self) -> None:
        class ExcessiveVadSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return None

            def process(self, _chunk, _cancel):
                return tuple(
                    VadEvent(
                        VadEventKind.SPEECH_STARTED
                        if frame % 2 == 0
                        else VadEventKind.SPEECH_ENDED,
                        frame,
                    )
                    for frame in range(257)
                )

            def finish(self, _cancel):
                return ()


        class ExcessiveVadProvider:
            def open_session(self, _format):
                return ExcessiveVadSession()


        format = PcmStreamFormat(16000)
        capture = FakeStreamingCaptureProvider(
            [PcmChunk(b"\x01\x00" * 300, format, 0)]
        )
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            capture,
            FakeAsrProvider([Transcript("raw", "normalized")]),
            vad=ExcessiveVadProvider(),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        self.assertTrue(capture.started.wait(timeout=1))
        gateway.release_ptt()
        self._wait_for_state(gateway, VoicePttState.IDLE)

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        self.assertFalse(
            any(event.audio_frame_index is not None for event in emitted)
        )
        self.assertIn(VoiceEventKind.INPUT_ACTIVITY_FAILED, [e.kind for e in emitted])
        self.assertIn(VoiceEventKind.TRANSCRIPT_READY, [e.kind for e in emitted])

    def test_legacy_capture_reports_vad_fallback_and_still_transcribes(self) -> None:
        events: Queue[VoiceEvent] = Queue()
        asr = FakeAsrProvider([Transcript("raw", "normalized")])
        gateway = self._gateway(
            FakeCaptureProvider([self._audio()]),
            asr,
            vad=FakeVadProvider([]),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        gateway.release_ptt()
        self._wait_for_state(gateway, VoicePttState.IDLE)

        kinds = self._drain_event_kinds(events)
        self.assertIn(VoiceEventKind.INPUT_ACTIVITY_FAILED, kinds)
        self.assertIn(VoiceEventKind.TRANSCRIPT_READY, kinds)
        self.assertEqual(len(asr.requests), 1)

    def test_ptt_cancellation_remains_terminal_with_vad_enabled(self) -> None:
        format = PcmStreamFormat(16000)
        capture = FakeStreamingCaptureProvider(
            [PcmChunk(b"\x01\x00" * 160, format, 0)]
        )
        events: Queue[VoiceEvent] = Queue()
        gateway = self._gateway(
            capture,
            FakeAsrProvider([Transcript("raw", "normalized")]),
            vad=FakeVadProvider([]),
            event_sink=events.put,
        )
        gateway.register_prompt_sink(lambda _target, _transcript: None)

        gateway.press_ptt(self.target)
        self.assertTrue(capture.started.wait(timeout=1))
        gateway.cancel_ptt()
        self._wait_for_state(gateway, VoicePttState.IDLE)

        kinds = self._drain_event_kinds(events)
        self.assertIn(VoiceEventKind.PTT_CANCELLED, kinds)
        self.assertNotIn(VoiceEventKind.TRANSCRIBING, kinds)

    def test_voice_activity_event_metadata_is_strictly_scoped(self) -> None:
        started = VoiceEvent(
            VoiceEventKind.INPUT_SPEECH_STARTED,
            self.target,
            audio_frame_index=0,
        )
        self.assertEqual(started.audio_frame_index, 0)

        with self.assertRaisesRegex(ValueError, "requires an audio frame"):
            VoiceEvent(VoiceEventKind.INPUT_SPEECH_ENDED, self.target)
        with self.assertRaisesRegex(ValueError, "only valid for input speech"):
            VoiceEvent(
                VoiceEventKind.TRANSCRIBING,
                self.target,
                audio_frame_index=1,
            )
        with self.assertRaisesRegex(ValueError, "error_code"):
            VoiceEvent(VoiceEventKind.INPUT_ACTIVITY_FAILED, self.target)

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
        vad=None,
        event_sink=None,
        max_capture_bytes: int = 1 << 20,
        max_speech_items: int = 8,
    ) -> VoiceGateway:
        gateway = VoiceGateway(
            capture_provider=capture,
            asr_provider=asr,
            tts_provider=FakeTtsProvider(),
            playback_provider=playback or FakePlaybackProvider(),
            vad_provider=vad,
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
