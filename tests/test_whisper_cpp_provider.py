import ctypes
import struct
from threading import Event, Thread
import time
import unittest

from voice.deskhelm_voice import (
    CapturedAudio,
    VoiceCancelled,
    VoiceNoTranscript,
    WhisperCppAsrProvider,
)


class _FakeFunction:
    def __init__(self, callback):
        self._callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._callback(*args)


class _FakeLibrary:
    def __init__(self, transcribe):
        self.created = []
        self.freed = []
        self.transcribe_calls = []
        self.deskhelm_whisper_shim_abi = _FakeFunction(
            lambda: b"deskhelm_whisper_shim_v1"
        )
        self.deskhelm_whisper_create = _FakeFunction(self._create)
        self.deskhelm_whisper_free = _FakeFunction(self._free)
        self.deskhelm_whisper_transcribe = _FakeFunction(transcribe)
        self.deskhelm_whisper_last_error = _FakeFunction(
            lambda _context: b""
        )

    def _create(self, model_path, threads):
        self.created.append((model_path, threads))
        return ctypes.c_void_p(7)

    def _free(self, context):
        self.freed.append(context)


def _audio(values=(0, 16384, -16384)):
    return CapturedAudio(
        struct.pack("<" + "h" * len(values), *values),
        sample_rate_hz=16_000,
    )


class WhisperCppProviderTests(unittest.TestCase):
    def test_load_is_lazy_and_success_converts_pcm_without_partials(self) -> None:
        observed = {}

        def transcribe(
            _context,
            samples,
            sample_count,
            language,
            _abort,
            output,
            _output_size,
        ):
            observed["sample_count"] = sample_count
            observed["samples"] = [samples[index] for index in range(sample_count)]
            observed["language"] = language
            ctypes.memmove(output, "运行测试".encode("utf-8"), len("运行测试".encode("utf-8")))
            return 0

        library = _FakeLibrary(transcribe)
        provider = WhisperCppAsrProvider(
            "/tmp/libdeskhelm-whisper.so",
            "/models/ggml-base.bin",
            cpu_threads=3,
            language="zh",
            library_loader=lambda _path: library,
        )
        self.assertEqual(library.created, [])
        result = provider.transcribe_streaming(_audio(), Event())
        self.assertEqual(result.transcript.normalized_text, "运行测试")
        self.assertIsNone(result.first_partial_latency_ms)
        self.assertEqual(
            library.created,
            [(b"/models/ggml-base.bin", 3)],
        )
        self.assertEqual(observed["sample_count"], 3)
        self.assertAlmostEqual(observed["samples"][1], 0.5, places=5)
        self.assertAlmostEqual(observed["samples"][2], -0.5, places=5)
        self.assertEqual(observed["language"], b"zh")
        provider.close()
        self.assertEqual(len(library.freed), 1)

    def test_validation_precedes_native_load(self) -> None:
        library = _FakeLibrary(lambda *_args: 0)
        provider = WhisperCppAsrProvider(
            "/tmp/libdeskhelm-whisper.so",
            "/models/ggml-base.bin",
            library_loader=lambda _path: library,
        )
        with self.assertRaisesRegex(ValueError, "16 kHz mono"):
            provider.transcribe(CapturedAudio(b"\x00\x00", 8_000), Event())
        self.assertEqual(library.created, [])
        with self.assertRaisesRegex(ValueError, "language"):
            WhisperCppAsrProvider(
                "/tmp/libdeskhelm-whisper.so",
                "/models/ggml-base.bin",
                language="not a language",
            )

    def test_status_mapping_and_failure_releases_serialization_lock(self) -> None:
        calls = []

        def transcribe(
            _context,
            _samples,
            _sample_count,
            _language,
            _abort,
            output,
            _output_size,
        ):
            calls.append(True)
            if len(calls) == 1:
                return 3
            ctypes.memmove(output, b"ok", 2)
            return 0

        library = _FakeLibrary(transcribe)
        provider = WhisperCppAsrProvider(
            "/tmp/libdeskhelm-whisper.so",
            "/models/ggml-base.bin",
            library_loader=lambda _path: library,
        )
        with self.assertRaisesRegex(RuntimeError, "transcription failed"):
            provider.transcribe(_audio(), Event())
        self.assertEqual(provider.transcribe(_audio(), Event()).normalized_text, "ok")

        empty = _FakeLibrary(lambda *_args: 2)
        empty_provider = WhisperCppAsrProvider(
            "/tmp/libdeskhelm-whisper.so",
            "/models/ggml-base.bin",
            library_loader=lambda _path: empty,
        )
        with self.assertRaises(VoiceNoTranscript):
            empty_provider.transcribe(_audio(), Event())

    def test_close_allows_explicit_reload_after_native_restart(self) -> None:
        def transcribe(
            _context,
            _samples,
            _sample_count,
            _language,
            _abort,
            output,
            _output_size,
        ):
            ctypes.memmove(output, b"ok", 2)
            return 0

        library = _FakeLibrary(transcribe)
        provider = WhisperCppAsrProvider(
            "/tmp/libdeskhelm-whisper.so",
            "/models/ggml-base.bin",
            library_loader=lambda _path: library,
        )

        self.assertEqual(
            provider.transcribe(_audio(), Event()).normalized_text,
            "ok",
        )
        provider.close()
        self.assertEqual(
            provider.transcribe(_audio(), Event()).normalized_text,
            "ok",
        )
        provider.close()

        self.assertEqual(len(library.created), 2)
        self.assertEqual(len(library.freed), 2)

    def test_cancel_flag_reaches_native_call(self) -> None:
        entered = Event()

        def transcribe(
            _context,
            _samples,
            _sample_count,
            _language,
            abort_pointer,
            _output,
            _output_size,
        ):
            entered.set()
            abort = ctypes.cast(
                abort_pointer,
                ctypes.POINTER(ctypes.c_int),
            )
            while not abort.contents.value:
                time.sleep(0.002)
            return 1

        library = _FakeLibrary(transcribe)
        provider = WhisperCppAsrProvider(
            "/tmp/libdeskhelm-whisper.so",
            "/models/ggml-base.bin",
            library_loader=lambda _path: library,
        )
        cancel = Event()
        result = []

        def run():
            try:
                provider.transcribe(_audio(), cancel)
            except Exception as error:  # noqa: BLE001 - assert exact outcome below
                result.append(error)

        worker = Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(1))
        cancel.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], VoiceCancelled)

    def test_transcript_character_limit_is_fixed(self) -> None:
        text = b"a" * 4097

        def transcribe(
            _context,
            _samples,
            _sample_count,
            _language,
            _abort,
            output,
            _output_size,
        ):
            ctypes.memmove(output, text, len(text))
            return 0

        library = _FakeLibrary(transcribe)
        provider = WhisperCppAsrProvider(
            "/tmp/libdeskhelm-whisper.so",
            "/models/ggml-base.bin",
            library_loader=lambda _path: library,
        )
        with self.assertRaisesRegex(RuntimeError, "transcript exceeds"):
            provider.transcribe(_audio(), Event())


if __name__ == "__main__":
    unittest.main()
