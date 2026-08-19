import hashlib
import json
import math
from pathlib import Path
import tempfile
from threading import Event
import unittest
import wave

from voice.deskhelm_voice import (
    AsrRunManifest,
    CapturedAudio,
    ParaformerStreamingAsrProvider,
    SenseVoiceOfflineAsrProvider,
    StreamingAsrResult,
    Transcript,
    VoiceCancelled,
    VoiceNoTranscript,
    load_prepared_asr_set,
)
from voice.deskhelm_voice.benchmark import (
    BenchmarkAudioSample,
    BenchmarkIdentity,
    BenchmarkStatus,
    run_asr_benchmark,
    summarize_asr,
)


MANIFEST_PATH = Path("voice/benchmarks/asr-external-v1.json")


class _MeasuredFakeProvider:
    def transcribe_streaming(self, audio, cancel):
        return StreamingAsrResult(Transcript("zero", "zero"), 12.5)


class AsrProviderTests(unittest.TestCase):
    def test_external_manifest_has_pinned_licensed_sources(self) -> None:
        manifest = AsrRunManifest.load(MANIFEST_PATH)

        self.assertEqual(manifest.sample_rate_hz, 16000)
        self.assertEqual(len(manifest.sources), 8)
        self.assertEqual(manifest.sources[0].license, "Apache-2.0")
        self.assertIn("unverified", {source.license for source in manifest.sources})
        self.assertTrue(
            all(len(source.revision) == 40 for source in manifest.sources)
        )

    def test_prepared_set_validates_checksum_duration_and_corpus(self) -> None:
        manifest = AsrRunManifest.load(MANIFEST_PATH)
        pcm = b"\x01\x00" * 1600
        with tempfile.TemporaryDirectory() as directory:
            prepared = Path(directory)
            entries = []
            for source in manifest.sources:
                path = prepared / f"{source.utterance_id}.wav"
                with wave.open(str(path), "wb") as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(16000)
                    stream.writeframes(pcm)
                entries.append(
                    {
                        "utterance_id": source.utterance_id,
                        "file_name": path.name,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "audio_duration_ms": 100.0,
                    }
                )
            (prepared / "index.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "manifest_name": manifest.name,
                        "samples": entries,
                    }
                ),
                encoding="utf-8",
            )

            corpus, samples = load_prepared_asr_set(MANIFEST_PATH, prepared)
            self.assertEqual(len(corpus.utterances), 8)
            self.assertEqual(len(samples), 8)
            self.assertEqual(samples[0].audio_duration_ms, 100)
            first_path = prepared / entries[0]["file_name"]
            first_path.write_bytes(first_path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_prepared_asr_set(MANIFEST_PATH, prepared)

    def test_benchmark_records_provider_reported_first_partial_latency(self) -> None:
        manifest = AsrRunManifest.load(MANIFEST_PATH)
        source = manifest.sources[1]
        identity = BenchmarkIdentity(
            run_id="asr-run",
            provider_name="fake-streaming",
            provider_version="1",
            model_name="fake",
            model_version="1",
            provider_license="test-only",
            model_license="test-only",
            system_profile="unit-test",
            device="cpu",
        )
        sample = source.utterance_id
        observation = run_asr_benchmark(
            _MeasuredFakeProvider(),
            identity,
            (
                BenchmarkAudioSample(
                    sample,
                    CapturedAudio(b"\x00\x00" * 1600, 16000),
                    100,
                ),
            ),
        )[0]

        self.assertEqual(observation.status, BenchmarkStatus.OK)
        self.assertEqual(observation.transcript, "zero")
        self.assertEqual(observation.first_partial_latency_ms, 12.5)
        summary = summarize_asr(manifest.corpus(), (observation,))
        self.assertEqual(summary["first_partial_latency_ms_p50"], 12.5)
        self.assertEqual(summary["first_partial_latency_ms_p95"], 12.5)
        with self.assertRaisesRegex(ValueError, "first partial latency"):
            StreamingAsrResult(Transcript("zero", "zero"), math.inf)

    def test_paraformer_load_is_lazy_and_input_validation_precedes_runtime(self) -> None:
        created = []

        def factory(path, threads):
            created.append((path, threads))
            return object()

        provider = ParaformerStreamingAsrProvider(
            "/model",
            cpu_threads=3,
            model_factory=factory,
        )
        self.assertEqual(created, [])
        provider.load()
        provider.load()
        self.assertEqual(created, [("/model", 3)])
        with self.assertRaisesRegex(ValueError, "16 kHz mono"):
            provider.transcribe(
                CapturedAudio(b"\x00\x00", sample_rate_hz=8000),
                Event(),
            )
        cancelled = Event()
        cancelled.set()
        with self.assertRaises(VoiceCancelled):
            provider.transcribe(
                CapturedAudio(b"\x00\x00", sample_rate_hz=16000),
                cancelled,
            )
        with self.assertRaisesRegex(ValueError, "duration limit"):
            ParaformerStreamingAsrProvider(
                "/model",
                max_audio_seconds=math.inf,
            )

    def test_paraformer_reports_empty_recognition_without_private_text(self) -> None:
        class EmptyModel:
            def generate(self, **_kwargs):
                return [{"text": ""}]

        provider = ParaformerStreamingAsrProvider(
            "/model",
            model_factory=lambda _path, _threads: EmptyModel(),
        )

        with self.assertRaises(VoiceNoTranscript) as caught:
            provider.transcribe(
                CapturedAudio(b"\x00\x00" * 160, sample_rate_hz=16000),
                Event(),
            )

        self.assertEqual(str(caught.exception), "")

    def test_sensevoice_is_lazy_final_only_and_bounded(self) -> None:
        created = []

        class Result:
            text = "运行测试"

        class Stream:
            result = Result()

            def accept_waveform(self, sample_rate, samples):
                self.sample_rate = sample_rate
                self.sample_count = len(samples)

        class Recognizer:
            def create_stream(self):
                self.stream = Stream()
                return self.stream

            def decode_stream(self, stream):
                self.decoded = stream

        def factory(**kwargs):
            created.append(kwargs)
            return Recognizer()

        provider = SenseVoiceOfflineAsrProvider(
            "/model",
            cpu_threads=3,
            recognizer_factory=factory,
        )
        self.assertEqual(created, [])
        result = provider.transcribe_streaming(
            CapturedAudio(b"\x00\x01" * 160, sample_rate_hz=16000),
            Event(),
        )

        self.assertEqual(result.transcript.normalized_text, "运行测试")
        self.assertIsNone(result.first_partial_latency_ms)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["num_threads"], 3)
        self.assertEqual(created[0]["language"], "auto")
        self.assertTrue(created[0]["use_itn"])
        self.assertTrue(created[0]["model"].endswith("model.int8.onnx"))

        with self.assertRaisesRegex(ValueError, "16 kHz mono"):
            provider.transcribe(
                CapturedAudio(b"\x00\x00", sample_rate_hz=8000),
                Event(),
            )
        with self.assertRaisesRegex(ValueError, "language"):
            SenseVoiceOfflineAsrProvider("/model", language="invalid")

    def test_sensevoice_empty_and_cancelled_results_are_private(self) -> None:
        class Result:
            text = ""

        class Stream:
            result = Result()

            def accept_waveform(self, _sample_rate, _samples):
                pass

        class Recognizer:
            def create_stream(self):
                return Stream()

            def decode_stream(self, _stream):
                pass

        provider = SenseVoiceOfflineAsrProvider(
            "/model",
            recognizer_factory=lambda **_kwargs: Recognizer(),
        )
        audio = CapturedAudio(b"\x00\x00" * 160, sample_rate_hz=16000)
        with self.assertRaises(VoiceNoTranscript) as caught:
            provider.transcribe(audio, Event())
        self.assertEqual(str(caught.exception), "")

        cancelled = Event()
        cancelled.set()
        with self.assertRaises(VoiceCancelled):
            provider.transcribe(audio, cancelled)


if __name__ == "__main__":
    unittest.main()
