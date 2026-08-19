import argparse
from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

from voice.deskhelm_voice import (
    CapturedAudio,
    FakeVadProvider,
    StreamingAsrResult,
    Transcript,
    VadEvent,
    VadEventKind,
    VoiceNoTranscript,
)
from voice.deskhelm_voice.benchmark import BenchmarkCorpus


TOOL_PATH = Path("tools/run-local-asr-diagnostic.py")
SPEC = importlib.util.spec_from_file_location("run_local_asr_diagnostic", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
TOOL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TOOL
SPEC.loader.exec_module(TOOL)
CORPUS = BenchmarkCorpus.load(Path("voice/benchmarks/utterances-v1.json"))
UTTERANCE = next(
    item for item in CORPUS.utterances if item.utterance_id == "zh-repeat-01"
)


class _StreamingProvider:
    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe_streaming(self, _audio, _cancel):
        return StreamingAsrResult(Transcript(self.text, self.text), 12.5)


class _NoTranscriptProvider:
    def transcribe_streaming(self, _audio, _cancel):
        raise VoiceNoTranscript()


class _NoisyProvider:
    def transcribe_streaming(self, _audio, _cancel):
        print("private recognized output")
        return StreamingAsrResult(Transcript(UTTERANCE.text, UTTERANCE.text))


class _NoisyVadProvider:
    def __init__(self, events) -> None:
        self.provider = FakeVadProvider(events)

    def open_session(self, stream_format):
        print("private VAD output")
        return self.provider.open_session(stream_format)


class LocalAsrDiagnosticToolTests(unittest.TestCase):
    def test_live_audio_requires_explicit_confirmation_and_bounds(self) -> None:
        parsed = TOOL._parser().parse_args(
            ["--asr-model-directory", "/model"]
        )
        self.assertEqual(parsed.lead_in_seconds, 0.0)
        self.assertEqual(parsed.asr_provider, "paraformer")
        self.assertEqual(parsed.vad_provider, "webrtc")

        args = argparse.Namespace(
            live_audio=False,
            capture_seconds=6.0,
            lead_in_seconds=3.0,
            cpu_threads=4,
        )
        with self.assertRaisesRegex(ValueError, "live-audio"):
            TOOL._validate_args(args)

        args.live_audio = True
        args.capture_seconds = 1.0
        with self.assertRaisesRegex(ValueError, "capture duration"):
            TOOL._validate_args(args)

    def test_success_summary_contains_metrics_without_private_content(self) -> None:
        audio = CapturedAudio(b"\x00\x10" * 1600, 16000)
        ticks = iter((1_000_000_000, 1_025_000_000))

        summary = TOOL._diagnose_audio(
            audio,
            _StreamingProvider(UTTERANCE.text),
            UTTERANCE,
            monotonic_ns=lambda: next(ticks),
        )

        self.assertEqual(summary["status"], "ok")
        self.assertTrue(summary["exact_match"])
        self.assertEqual(summary["character_error_rate"], 0)
        self.assertEqual(summary["keyword_accuracy"], 1)
        self.assertEqual(summary["first_partial_latency_ms"], 12.5)
        self.assertEqual(summary["final_asr_latency_ms"], 25.0)
        self.assertTrue(summary["requires_post_run_speech_confirmation"])
        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn(UTTERANCE.text, serialized)
        self.assertNotIn("audio_data", serialized)

    def test_no_transcript_keeps_signal_metadata_and_fixed_error(self) -> None:
        audio = CapturedAudio(b"\x00\x00" * 1600, 16000)
        ticks = iter((1_000_000_000, 1_010_000_000))

        summary = TOOL._diagnose_audio(
            audio,
            _NoTranscriptProvider(),
            UTTERANCE,
            monotonic_ns=lambda: next(ticks),
        )

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["error_code"], "voice_no_transcript")
        self.assertEqual(summary["input_level_hint"], "too_quiet")
        self.assertEqual(summary["transcript_chars"], 0)
        self.assertIsNone(summary["character_error_rate"])

    def test_vad_activity_is_aggregated_without_gating_asr(self) -> None:
        audio = CapturedAudio(b"\x00\x10" * 1600, 16000)
        output = StringIO()
        with redirect_stdout(output):
            summary = TOOL._diagnose_audio(
                audio,
                _StreamingProvider(UTTERANCE.text),
                UTTERANCE,
                vad_provider=_NoisyVadProvider(
                    [
                        VadEvent(VadEventKind.SPEECH_STARTED, 160),
                        VadEvent(VadEventKind.SPEECH_ENDED, 640),
                        VadEvent(VadEventKind.SPEECH_STARTED, 800),
                        VadEvent(VadEventKind.SPEECH_ENDED, 1440),
                    ]
                ),
            )
        self.assertEqual(output.getvalue(), "")

        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["speech_activity_status"], "ok")
        self.assertEqual(summary["speech_segment_count"], 2)
        self.assertEqual(summary["speech_active_ms"], 70.0)
        self.assertEqual(summary["speech_active_fraction"], 0.7)
        self.assertEqual(summary["first_speech_start_ms"], 10.0)
        self.assertEqual(summary["last_speech_end_ms"], 90.0)

    def test_vad_failure_is_fixed_and_asr_still_completes(self) -> None:
        audio = CapturedAudio(b"\x00\x10" * 1600, 16000)
        providers = (
            FakeVadProvider([], fail=True),
            FakeVadProvider([VadEvent(VadEventKind.SPEECH_ENDED, 100)]),
        )
        for vad_provider in providers:
            with self.subTest(vad_provider=vad_provider):
                summary = TOOL._diagnose_audio(
                    audio,
                    _StreamingProvider(UTTERANCE.text),
                    UTTERANCE,
                    vad_provider=vad_provider,
                )

                self.assertEqual(summary["status"], "ok")
                self.assertEqual(summary["speech_activity_status"], "failed")
                self.assertEqual(
                    summary["speech_activity_error_code"],
                    "voice_vad_failed",
                )
                self.assertIsNone(summary["speech_active_ms"])

    def test_provider_output_is_suppressed_and_capture_failure_is_fixed(self) -> None:
        audio = CapturedAudio(b"\x00\x10" * 1600, 16000)
        output = StringIO()
        with redirect_stdout(output):
            summary = TOOL._diagnose_audio(audio, _NoisyProvider(), UTTERANCE)
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(output.getvalue(), "")

        failure = TOOL._capture_failure_summary()
        self.assertEqual(failure["error_code"], "voice_input_failed")
        self.assertEqual(failure["input_level_hint"], "unavailable")
        self.assertTrue(failure["requires_post_run_speech_confirmation"])
        serialized = json.dumps(failure)
        self.assertNotIn("exception", serialized)

    def test_signal_hint_identifies_full_scale_and_model_preflight(self) -> None:
        audio = CapturedAudio(b"\xff\x7f" * 160, 16000)
        summary = TOOL._signal_summary(audio)
        self.assertEqual(summary["input_level_hint"], "possible_clipping")
        self.assertEqual(summary["clipped_sample_fraction"], 1)

        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)
            with self.assertRaisesRegex(ValueError, "model.pt"):
                TOOL._validate_model_directory(model, "paraformer")
            for name in TOOL.PARAFORMER_ARTIFACTS:
                (model / name).write_bytes(b"test")
            TOOL._validate_model_directory(model, "paraformer")

        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)
            with self.assertRaisesRegex(ValueError, "model.int8.onnx"):
                TOOL._validate_model_directory(model, "sensevoice")
            for name in TOOL.SENSEVOICE_ARTIFACTS:
                (model / name).write_bytes(b"test")
            TOOL._validate_model_directory(model, "sensevoice")

    def test_loads_only_named_public_utterance(self) -> None:
        loaded = TOOL._load_utterance(
            Path("voice/benchmarks/utterances-v1.json"),
            "zh-repeat-01",
        )
        self.assertEqual(loaded, UTTERANCE)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            TOOL._load_utterance(
                Path("voice/benchmarks/utterances-v1.json"),
                "missing",
            )


if __name__ == "__main__":
    unittest.main()
