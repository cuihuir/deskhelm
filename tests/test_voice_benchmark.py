from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from voice.deskhelm_voice import (
    CapturedAudio,
    FakeAsrProvider,
    FakeTtsProvider,
    Transcript,
)
from voice.deskhelm_voice.benchmark import (
    MAX_NDJSON_RECORD_BYTES,
    MAX_OBSERVATION_FILE_BYTES,
    MAX_TEXT_CHARS,
    AsrBenchmarkObservation,
    BenchmarkAudioSample,
    BenchmarkCorpus,
    BenchmarkIdentity,
    BenchmarkStatus,
    TtsBenchmarkObservation,
    character_error_rate,
    keyword_accuracy,
    main,
    read_asr_observations,
    run_asr_benchmark,
    run_tts_benchmark,
    summarize_asr,
    summarize_tts,
    word_error_rate,
    write_observations,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "voice" / "benchmarks" / "utterances-v1.json"


class VoiceBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = BenchmarkCorpus.load(CORPUS_PATH)
        self.identity = BenchmarkIdentity(
            run_id="run-1",
            provider_name="fake",
            provider_version="1",
            model_name="fake-model",
            model_version="1",
            provider_license="test-only",
            model_license="test-only",
            system_profile="unit-test",
            device="cpu",
        )

    def test_versioned_corpus_covers_required_categories(self) -> None:
        tags = {
            tag
            for utterance in self.corpus.utterances
            for tag in utterance.tags
        }

        self.assertEqual(len(self.corpus.utterances), 12)
        self.assertTrue(
            {
                "chinese",
                "english",
                "mixed",
                "command",
                "path",
                "url",
                "number",
                "negation",
                "repetition",
                "long",
            }.issubset(tags)
        )

    def test_accuracy_metrics_preserve_code_sensitive_content(self) -> None:
        self.assertEqual(character_error_rate("请运行测试", "请运行测试"), 0)
        self.assertGreater(character_error_rate("Python 3.11", "Python 3.10"), 0)
        self.assertEqual(
            word_error_rate("run focused tests", "run the focused tests"),
            1 / 3,
        )
        self.assertEqual(
            keyword_accuracy(
                ("gateway.py", "press_ptt"),
                "Open gateway.py and inspect press_ptt.",
            ),
            1,
        )

    def test_fake_runners_emit_bounded_provider_neutral_observations(self) -> None:
        utterance = self.corpus.utterances[0]
        asr = FakeAsrProvider([Transcript(utterance.text, utterance.text)])
        samples = (
            BenchmarkAudioSample(
                utterance_id=utterance.utterance_id,
                audio=CapturedAudio(b"pcm", sample_rate_hz=16000),
                audio_duration_ms=1000,
            ),
        )

        asr_observations = run_asr_benchmark(asr, self.identity, samples)
        tts_corpus = BenchmarkCorpus("one", (utterance,))
        tts_observations = run_tts_benchmark(
            FakeTtsProvider(), self.identity, tts_corpus
        )

        self.assertEqual(asr_observations[0].status, BenchmarkStatus.OK)
        self.assertEqual(asr_observations[0].transcript, utterance.text)
        self.assertGreater(tts_observations[0].output_bytes, 0)
        self.assertEqual(summarize_asr(tts_corpus, asr_observations)["cer_mean"], 0)
        self.assertEqual(summarize_tts(tts_corpus, tts_observations)["failed"], 0)

    def test_failed_provider_records_fixed_code_without_exception_text(self) -> None:
        utterance = self.corpus.utterances[0]
        observations = run_asr_benchmark(
            FakeAsrProvider([]),
            self.identity,
            (
                BenchmarkAudioSample(
                    utterance.utterance_id,
                    CapturedAudio(b"pcm", sample_rate_hz=16000),
                    100,
                ),
            ),
        )

        self.assertEqual(observations[0].status, BenchmarkStatus.FAILED)
        self.assertEqual(observations[0].error_code, "provider_failed")
        self.assertEqual(observations[0].transcript, "")

    def test_ndjson_round_trip_and_unknown_utterance_rejection(self) -> None:
        utterance = self.corpus.utterances[0]
        observation = AsrBenchmarkObservation(
            identity=self.identity,
            utterance_id=utterance.utterance_id,
            repetition=1,
            status=BenchmarkStatus.OK,
            audio_duration_ms=1000,
            final_latency_ms=100,
            process_cpu_ms=50,
            transcript=utterance.text,
        )
        stream = StringIO()
        self.assertEqual(write_observations(stream, [observation]), 1)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.ndjson"
            path.write_text(stream.getvalue(), encoding="utf-8")
            loaded = read_asr_observations(path)

        self.assertEqual(loaded, (observation,))
        unknown = AsrBenchmarkObservation(
            identity=self.identity,
            utterance_id="unknown",
            repetition=1,
            status=BenchmarkStatus.OK,
            audio_duration_ms=1000,
            final_latency_ms=100,
            process_cpu_ms=50,
            transcript="unknown",
        )
        with self.assertRaisesRegex(ValueError, "unknown utterance"):
            summarize_asr(self.corpus, [unknown])

    def test_observation_validation_rejects_private_failure_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain transcript"):
            AsrBenchmarkObservation(
                identity=self.identity,
                utterance_id="zh-basic-01",
                repetition=1,
                status=BenchmarkStatus.FAILED,
                audio_duration_ms=1000,
                final_latency_ms=100,
                process_cpu_ms=50,
                transcript="provider exception included private text",
                error_code="provider_failed",
            )
        with self.assertRaisesRegex(ValueError, "must not contain output"):
            TtsBenchmarkObservation(
                identity=self.identity,
                utterance_id="zh-basic-01",
                repetition=1,
                status=BenchmarkStatus.FAILED,
                synthesis_latency_ms=100,
                process_cpu_ms=50,
                output_bytes=20,
                error_code="provider_failed",
            )

    def test_metric_and_ndjson_inputs_are_bounded_before_processing(self) -> None:
        with self.assertRaisesRegex(ValueError, "too long"):
            character_error_rate("a" * (MAX_TEXT_CHARS + 1), "a")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.ndjson"
            path.write_bytes(b"{" + b"x" * MAX_NDJSON_RECORD_BYTES + b"}\n")
            with self.assertRaisesRegex(ValueError, "size limit"):
                read_asr_observations(path)

            aggregate_path = Path(directory) / "oversized-file.ndjson"
            with aggregate_path.open("wb") as stream:
                stream.truncate(MAX_OBSERVATION_FILE_BYTES + 1)
            with self.assertRaisesRegex(ValueError, "file exceeds"):
                read_asr_observations(aggregate_path)

    def test_summary_rejects_mixed_provider_runs(self) -> None:
        utterance = self.corpus.utterances[0]
        first = AsrBenchmarkObservation(
            identity=self.identity,
            utterance_id=utterance.utterance_id,
            repetition=1,
            status=BenchmarkStatus.OK,
            audio_duration_ms=1000,
            final_latency_ms=100,
            process_cpu_ms=50,
            transcript=utterance.text,
        )
        other_identity = BenchmarkIdentity(
            run_id="run-2",
            provider_name="other",
            provider_version="1",
            model_name="other-model",
            model_version="1",
            provider_license="test-only",
            model_license="test-only",
            system_profile="unit-test",
            device="cpu",
        )
        second = AsrBenchmarkObservation(
            identity=other_identity,
            utterance_id=utterance.utterance_id,
            repetition=1,
            status=BenchmarkStatus.OK,
            audio_duration_ms=1000,
            final_latency_ms=100,
            process_cpu_ms=50,
            transcript=utterance.text,
        )

        with self.assertRaisesRegex(ValueError, "share one identity"):
            summarize_asr(self.corpus, [first, second])

    def test_cli_scores_asr_ndjson(self) -> None:
        utterance = self.corpus.utterances[0]
        observation = AsrBenchmarkObservation(
            identity=self.identity,
            utterance_id=utterance.utterance_id,
            repetition=1,
            status=BenchmarkStatus.OK,
            audio_duration_ms=1000,
            final_latency_ms=100,
            process_cpu_ms=50,
            transcript=utterance.text,
        )
        output = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.ndjson"
            with path.open("w", encoding="utf-8") as stream:
                write_observations(stream, [observation])
            with redirect_stdout(output):
                result = main(
                    [
                        "score-asr",
                        "--corpus",
                        str(CORPUS_PATH),
                        "--observations",
                        str(path),
                    ]
                )

        summary = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(summary["cer_mean"], 0)
        self.assertEqual(summary["identity"]["run_id"], "run-1")

    def test_corpus_parser_rejects_non_object_utterance(self) -> None:
        data = self.corpus.to_dict()
        data["utterances"].append("not-an-object")

        with self.assertRaisesRegex(ValueError, "invalid benchmark corpus"):
            BenchmarkCorpus.from_dict(json.loads(json.dumps(data)))


if __name__ == "__main__":
    unittest.main()
