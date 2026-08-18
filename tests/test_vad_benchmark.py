from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from voice.deskhelm_voice import (
    FakeVadProvider,
    PcmChunk,
    PcmStreamFormat,
    SpeechSegment,
    VadEvent,
    VadEventKind,
)
from voice.deskhelm_voice.benchmark import (
    BenchmarkIdentity,
    BenchmarkStatus,
    VadBenchmarkObservation,
    VadBenchmarkSample,
    main,
    read_vad_observations,
    run_vad_benchmark,
    summarize_vad,
    write_observations,
)


class VadBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = BenchmarkIdentity(
            run_id="vad-run-1",
            provider_name="fake-vad",
            provider_version="1",
            model_name="fake-boundaries",
            model_version="1",
            provider_license="test-only",
            model_license="test-only",
            system_profile="unit-test",
            device="cpu",
        )

    def test_pcm_chunks_use_contiguous_absolute_frame_positions(self) -> None:
        format = PcmStreamFormat(sample_rate_hz=1000)
        chunk = PcmChunk(b"\x00\x00" * 100, format, start_frame=200)

        self.assertEqual(chunk.frame_count, 100)
        self.assertEqual(chunk.end_frame, 300)
        self.assertEqual(chunk.duration_seconds, 0.1)
        with self.assertRaisesRegex(ValueError, "complete frames"):
            PcmChunk(b"odd", format, start_frame=0)
        with self.assertRaisesRegex(ValueError, "size limit"):
            PcmChunk(b"\x00\x00" * ((1 << 19) + 1), format, start_frame=0)

    def test_sample_rejects_gaps_format_changes_and_overlapping_segments(self) -> None:
        format = PcmStreamFormat(sample_rate_hz=1000)
        other_format = PcmStreamFormat(sample_rate_hz=16000)
        first = PcmChunk(b"\x00\x00" * 100, format, 0)

        with self.assertRaisesRegex(ValueError, "contiguous"):
            VadBenchmarkSample(
                "gap",
                (first, PcmChunk(b"\x00\x00" * 100, format, 101)),
                (),
            )
        with self.assertRaisesRegex(ValueError, "format changed"):
            VadBenchmarkSample(
                "format",
                (first, PcmChunk(b"\x00\x00" * 100, other_format, 100)),
                (),
            )
        with self.assertRaisesRegex(ValueError, "overlap"):
            VadBenchmarkSample(
                "segments",
                (first,),
                (SpeechSegment(10, 60), SpeechSegment(50, 80)),
            )

    def test_fake_streaming_runner_measures_overlap_and_detection_delay(self) -> None:
        sample = self._sample((SpeechSegment(100, 300),))
        provider = FakeVadProvider(
            (
                VadEvent(VadEventKind.SPEECH_STARTED, 150),
                VadEvent(VadEventKind.SPEECH_ENDED, 350),
            )
        )

        observations = run_vad_benchmark(
            provider, self.identity, (sample,), repetitions=2
        )

        self.assertEqual(provider.sessions_opened, 2)
        self.assertEqual(len(observations), 2)
        observation = observations[0]
        self.assertEqual(observation.status, BenchmarkStatus.OK)
        self.assertEqual(observation.reference_speech_ms, 200)
        self.assertEqual(observation.predicted_speech_ms, 200)
        self.assertEqual(observation.true_positive_ms, 150)
        self.assertEqual(observation.false_positive_ms, 50)
        self.assertEqual(observation.false_negative_ms, 50)
        self.assertEqual(observation.first_speech_detection_delay_ms, 50)

        summary = summarize_vad(observations)
        self.assertEqual(summary["speech_precision"], 0.75)
        self.assertEqual(summary["speech_recall"], 0.75)
        self.assertEqual(summary["speech_f1"], 0.75)
        self.assertEqual(summary["failed"], 0)

    def test_silence_only_run_has_no_undefined_output_payload(self) -> None:
        observations = run_vad_benchmark(
            FakeVadProvider(()), self.identity, (self._sample(()),)
        )

        observation = observations[0]
        self.assertEqual(observation.predicted_segments, 0)
        self.assertIsNone(observation.first_speech_detection_delay_ms)
        summary = summarize_vad(observations)
        self.assertIsNone(summary["speech_precision"])
        self.assertIsNone(summary["speech_recall"])

    def test_provider_failure_and_invalid_events_use_fixed_failure_record(self) -> None:
        sample = self._sample((SpeechSegment(100, 300),))
        for provider in (
            FakeVadProvider((), fail=True),
            FakeVadProvider(
                (
                    VadEvent(VadEventKind.SPEECH_STARTED, 100),
                    VadEvent(VadEventKind.SPEECH_STARTED, 200),
                )
            ),
        ):
            observation = run_vad_benchmark(
                provider, self.identity, (sample,)
            )[0]
            self.assertEqual(observation.status, BenchmarkStatus.FAILED)
            self.assertEqual(observation.error_code, "provider_failed")
            self.assertEqual(observation.predicted_segments, 0)
            self.assertEqual(observation.reference_speech_ms, 0)

    def test_failed_observation_rejects_partial_provider_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain output"):
            VadBenchmarkObservation(
                identity=self.identity,
                sample_id="sample",
                repetition=1,
                status=BenchmarkStatus.FAILED,
                sample_rate_hz=1000,
                channels=1,
                sample_format="s16le",
                audio_duration_ms=400,
                max_chunk_duration_ms=100,
                processing_latency_ms=1,
                process_cpu_ms=1,
                reference_speech_ms=100,
                predicted_speech_ms=100,
                true_positive_ms=100,
                false_positive_ms=0,
                false_negative_ms=0,
                predicted_segments=1,
                error_code="provider_failed",
            )

    def test_vad_ndjson_round_trip_and_cli_summary(self) -> None:
        observation = run_vad_benchmark(
            FakeVadProvider(
                (
                    VadEvent(VadEventKind.SPEECH_STARTED, 100),
                    VadEvent(VadEventKind.SPEECH_ENDED, 300),
                )
            ),
            self.identity,
            (self._sample((SpeechSegment(100, 300),)),),
        )[0]
        output = StringIO()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vad.ndjson"
            with path.open("w", encoding="utf-8") as stream:
                self.assertEqual(write_observations(stream, (observation,)), 1)
            self.assertEqual(read_vad_observations(path), (observation,))
            with redirect_stdout(output):
                result = main(
                    ["summarize-vad", "--observations", str(path)]
                )

        summary = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(summary["summary_type"], "vad")
        self.assertEqual(summary["speech_f1"], 1)

    @staticmethod
    def _sample(
        reference_segments: tuple[SpeechSegment, ...],
    ) -> VadBenchmarkSample:
        format = PcmStreamFormat(sample_rate_hz=1000)
        chunks = tuple(
            PcmChunk(b"\x00\x00" * 100, format, start_frame)
            for start_frame in (0, 100, 200, 300)
        )
        return VadBenchmarkSample("sample-1", chunks, reference_segments)


if __name__ == "__main__":
    unittest.main()
