from array import array
import importlib.util
from io import StringIO
from pathlib import Path
import sys
from threading import Event
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

from voice import deskhelm_voice as _deskhelm_voice
from deskhelm_voice import (
    FakeStreamingCaptureProvider,
    FakeVadProvider,
    PcmChunk,
    PcmStreamFormat,
    VadEvent,
    VadEventKind,
)

sys.modules.setdefault("deskhelm_voice", _deskhelm_voice)


def _load_tool(name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PREPARE = _load_tool("prepare_vad_benchmark", "prepare-vad-benchmark.py")
RUNNER = _load_tool("run_vad_benchmark", "run-vad-benchmark.py")
LIVE = _load_tool("run_live_vad_diagnostic", "run-live-vad-diagnostic.py")


class _RejectingSelector:
    def register(self, _fileobj, _events) -> None:
        raise PermissionError("stdin cannot be polled")

    def close(self) -> None:
        pass


class _SelectorRejectedStdin:
    def __init__(self, line: str) -> None:
        self._line = line

    def fileno(self) -> int:
        return 0

    def readline(self, _limit: int) -> str:
        return self._line


class _BlockingSelectorRejectedStdin:
    def __init__(self) -> None:
        self.release = Event()

    def fileno(self) -> int:
        return 0

    def readline(self, _limit: int) -> str:
        self.release.wait()
        return ""


class VadToolTests(unittest.TestCase):
    def test_noise_mix_applies_requested_snr_without_clipping(self) -> None:
        speech = array("h", [1000] * 100)
        noise = array("h", [100] * 20)

        mixed_pcm, mix_scale = PREPARE._mix_background(
            speech.tobytes(),
            noise.tobytes(),
            [{"start_frame": 0, "end_frame": 100}],
            0,
        )

        mixed = array("h")
        mixed.frombytes(mixed_pcm)
        self.assertEqual(mix_scale, 1.0)
        self.assertTrue(all(sample == 2000 for sample in mixed))

    def test_runner_builds_bounded_webrtc_and_silero_sweeps(self) -> None:
        parser = RUNNER._parser()
        webrtc = parser.parse_args(
            [
                "--provider",
                "webrtc",
                "--manifest",
                "manifest.json",
                "--prepared",
                "prepared",
                "--observations",
                "observations.ndjson",
                "--sweep",
            ]
        )
        silero = parser.parse_args(
            [
                "--provider",
                "silero",
                "--manifest",
                "manifest.json",
                "--prepared",
                "prepared",
                "--observations",
                "observations.ndjson",
                "--silero-model",
                "silero.onnx",
                "--sweep",
            ]
        )

        self.assertEqual(
            [profile.webrtc_mode for profile in RUNNER._profile_specs(webrtc, parser)],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            [
                profile.silero_threshold
                for profile in RUNNER._profile_specs(silero, parser)
            ],
            [0.35, 0.5, 0.65],
        )

    def test_live_diagnostic_reports_only_derived_timing_and_segments(self) -> None:
        stream_format = PcmStreamFormat(sample_rate_hz=1000)
        capture = FakeStreamingCaptureProvider(
            (
                PcmChunk(b"\x01\x00" * 100, stream_format, 0),
                PcmChunk(b"\x02\x00" * 100, stream_format, 100),
            ),
            end_on_exhaustion=True,
        )
        vad = FakeVadProvider(
            (
                VadEvent(VadEventKind.SPEECH_STARTED, 50),
                VadEvent(VadEventKind.SPEECH_ENDED, 150),
            )
        )

        summary = LIVE.run_live_vad_diagnostic(
            capture,
            vad,
            seconds=1.0,
        )

        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["audio_duration_ms"], 200.0)
        self.assertEqual(summary["speech_active_ms"], 100.0)
        self.assertEqual(summary["event_count"], 2)
        self.assertEqual(summary["bytes_captured"], 400)
        self.assertEqual(summary["signal_peak_fraction"], 0.000061)
        self.assertEqual(summary["clipped_sample_fraction"], 0.0)
        self.assertEqual(summary["near_silence_fraction"], 1.0)
        self.assertEqual(summary["input_level_hint"], "too_quiet")
        serialized = LIVE.json.dumps(summary)
        self.assertNotIn("audio_data", serialized)
        self.assertNotIn("audio_payload", serialized)
        self.assertNotIn("private", serialized.lower())

    def test_signal_summary_reports_clipping_without_retaining_pcm(self) -> None:
        samples = array("h", [0, 1000, 32767, -32768])
        totals = LIVE._pcm_signal_totals(samples.tobytes())

        summary = LIVE._signal_summary(
            sample_count=totals[0],
            sum_squares=totals[1],
            peak_sample=totals[2],
            clipped_samples=totals[3],
            near_silence_samples=totals[4],
        )

        self.assertEqual(summary["signal_peak_fraction"], 1.0)
        self.assertEqual(summary["clipped_sample_fraction"], 0.5)
        self.assertEqual(summary["input_level_hint"], "possible_clipping")

    def test_live_diagnostic_requires_explicit_audio_confirmation(self) -> None:
        args = LIVE._parser().parse_args([])
        self.assertFalse(args.await_phrase_ready)
        self.assertEqual(args.phrase_ready_timeout_seconds, 60.0)
        self.assertEqual(args.utterance_id, "zh-repeat-01")

        with self.assertRaisesRegex(ValueError, "live-audio"):
            LIVE._validate_args(args)

        args.live_audio = True
        args.phrase_ready_timeout_seconds = 0.5
        with self.assertRaisesRegex(ValueError, "phrase-ready timeout"):
            LIVE._validate_args(args)

    def test_live_diagnostic_uses_a_versioned_public_phrase(self) -> None:
        utterance = LIVE._load_public_phrase(
            Path("voice/benchmarks/utterances-v1.json"),
            "zh-repeat-01",
        )

        self.assertEqual(utterance.utterance_id, "zh-repeat-01")
        self.assertIn("运行测试", utterance.text)

    def test_phrase_readiness_requires_exact_ready_line(self) -> None:
        with patch.object(LIVE.sys, "stdin", StringIO("ready\n")):
            self.assertTrue(LIVE._await_phrase_ready(1.0))
        with patch.object(LIVE.sys, "stdin", StringIO("not yet\n")):
            self.assertFalse(LIVE._await_phrase_ready(1.0))

    def test_phrase_readiness_handles_unpollable_stdin_with_a_timeout(self) -> None:
        with patch.object(LIVE.selectors, "DefaultSelector", _RejectingSelector):
            with patch.object(
                LIVE.sys,
                "stdin",
                _SelectorRejectedStdin("ready\n"),
            ):
                self.assertTrue(LIVE._await_phrase_ready(1.0))

            stdin = _BlockingSelectorRejectedStdin()
            started = time.monotonic()
            with patch.object(LIVE.sys, "stdin", stdin):
                self.assertFalse(LIVE._await_phrase_ready(0.01))
            self.assertLess(time.monotonic() - started, 0.2)
            stdin.release.set()


if __name__ == "__main__":
    unittest.main()
