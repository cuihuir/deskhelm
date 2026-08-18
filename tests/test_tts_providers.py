import json
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest

from voice.deskhelm_voice import (
    KokoroTtsProvider,
    PiperTtsProvider,
    SynthesizedAudio,
    VoiceCancelled,
)
from voice.deskhelm_voice.benchmark import (
    BenchmarkCorpus,
    BenchmarkIdentity,
    BenchmarkLanguage,
    BenchmarkUtterance,
    run_tts_benchmark,
    summarize_tts,
)
from voice.deskhelm_voice.tts_manifest import TtsCandidateManifest


MANIFEST_PATH = Path("voice/benchmarks/tts-candidates-v1.json")


class _StreamingFake:
    def synthesize_streaming(self, text, cancel):
        yield SynthesizedAudio(b"\x00\x00" * 100, 1000)
        yield SynthesizedAudio(b"\x00\x00" * 200, 1000)


class _PiperVoice:
    def synthesize(self, text):
        yield SimpleNamespace(
            sample_width=2,
            sample_channels=1,
            sample_rate=22050,
            audio_int16_bytes=b"\x01\x00" * 16,
        )


class _KokoroRuntime:
    def __init__(self, *args):
        self.languages = []

    def synthesize(self, text, language):
        self.languages.append(language)
        yield SynthesizedAudio(b"\x01\x00" * 16, 24000)


class TtsProviderTests(unittest.TestCase):
    def test_manifest_pins_licensed_candidates_and_artifacts(self) -> None:
        manifest = TtsCandidateManifest.load(MANIFEST_PATH)

        self.assertEqual(len(manifest.candidates), 2)
        piper = manifest.candidate("piper-chaowen-medium")
        kokoro = manifest.candidate("kokoro-v1-auto-zh-en")
        self.assertEqual(piper.provider_license, "GPL-3.0-or-later")
        self.assertEqual(piper.model_license, "CC0-1.0")
        self.assertEqual(kokoro.provider_license, "Apache-2.0")
        self.assertTrue(all(len(item.sha256) == 64 for item in piper.artifacts))

        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        data["candidates"][0]["candidate_id"] = "../outside"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid TTS manifest"):
                TtsCandidateManifest.load(path)

    def test_streaming_benchmark_records_first_audio_duration_and_rtf(self) -> None:
        corpus = BenchmarkCorpus(
            "tts-one",
            (
                BenchmarkUtterance(
                    "tts-1",
                    BenchmarkLanguage.EN_US,
                    "hello",
                ),
            ),
        )
        identity = BenchmarkIdentity(
            run_id="tts-run",
            provider_name="fake",
            provider_version="1",
            model_name="fake",
            model_version="1",
            provider_license="test-only",
            model_license="test-only",
            system_profile="unit-test",
            device="cpu",
        )
        wall_times = iter((0, 10_000_000, 30_000_000))
        cpu_times = iter((0, 5_000_000))
        observation = run_tts_benchmark(
            _StreamingFake(),
            identity,
            corpus,
            monotonic_ns=lambda: next(wall_times),
            process_time_ns=lambda: next(cpu_times),
        )[0]

        self.assertEqual(observation.first_audio_latency_ms, 10)
        self.assertEqual(observation.synthesis_latency_ms, 30)
        self.assertEqual(observation.audio_duration_ms, 300)
        summary = summarize_tts(corpus, (observation,))
        self.assertEqual(summary["first_audio_latency_ms_p95"], 10)
        self.assertEqual(summary["real_time_factor_mean"], 0.1)

    def test_piper_is_lazy_streaming_bounded_and_cancellable(self) -> None:
        created = []
        provider = PiperTtsProvider(
            "/model",
            "/config",
            "/resources",
            voice_factory=lambda model, config, resources, threads: (
                created.append((model, config, resources, threads))
                or _PiperVoice()
            ),
        )

        self.assertEqual(created, [])
        audio = provider.synthesize("你好", Event())
        self.assertEqual(
            created,
            [("/model", "/config", "/resources", 4)],
        )
        self.assertEqual(audio.sample_rate_hz, 22050)
        cancelled = Event()
        cancelled.set()
        with self.assertRaises(VoiceCancelled):
            provider.synthesize("你好", cancelled)

        bounded = PiperTtsProvider(
            "/model",
            "/config",
            "/resources",
            max_output_bytes=31,
            voice_factory=lambda *_args: _PiperVoice(),
        )
        with self.assertRaisesRegex(ValueError, "output exceeds"):
            bounded.synthesize("你好", Event())

        with self.assertRaisesRegex(ValueError, "thread count"):
            PiperTtsProvider(
                "/model",
                "/config",
                "/resources",
                cpu_threads=True,
            )

    def test_kokoro_selects_chinese_or_english_pipeline(self) -> None:
        created = []

        def factory(*args):
            runtime = _KokoroRuntime(*args)
            created.append(runtime)
            return runtime

        provider = KokoroTtsProvider(
            "/config",
            "/model",
            "/zh",
            "/en",
            runtime_factory=factory,
        )

        provider.synthesize("你好 DeskHelm", Event())
        provider.synthesize("Hello DeskHelm", Event())
        self.assertEqual(created[0].languages, ["zh", "en"])


if __name__ == "__main__":
    unittest.main()
