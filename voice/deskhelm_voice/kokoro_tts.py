from __future__ import annotations

from collections.abc import Iterable
import re
from threading import Event, Lock
from typing import Callable

from .models import SynthesizedAudio
from .piper_tts import _join_chunks, _validate_text
from .providers import VoiceCancelled


_CJK = re.compile(r"[\u3400-\u9fff]")


class KokoroTtsProvider:
    def __init__(
        self,
        config_path: str,
        model_path: str,
        chinese_voice_path: str,
        english_voice_path: str,
        *,
        cpu_threads: int = 4,
        max_text_chars: int = 4096,
        max_output_bytes: int = 64 << 20,
        runtime_factory: Callable[..., object] | None = None,
    ) -> None:
        for value, name in (
            (config_path, "config"),
            (model_path, "model"),
            (chinese_voice_path, "Chinese voice"),
            (english_voice_path, "English voice"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"Kokoro {name} path is invalid")
        if (
            not isinstance(cpu_threads, int)
            or isinstance(cpu_threads, bool)
            or not 1 <= cpu_threads <= 32
        ):
            raise ValueError("Kokoro CPU thread count is invalid")
        if (
            not isinstance(max_text_chars, int)
            or isinstance(max_text_chars, bool)
            or not 1 <= max_text_chars <= 65_536
        ):
            raise ValueError("Kokoro text limit is invalid")
        if (
            not isinstance(max_output_bytes, int)
            or isinstance(max_output_bytes, bool)
            or not 2 <= max_output_bytes <= 1 << 30
        ):
            raise ValueError("Kokoro output limit is invalid")
        self._paths = (
            config_path,
            model_path,
            chinese_voice_path,
            english_voice_path,
        )
        self._cpu_threads = cpu_threads
        self._max_text_chars = max_text_chars
        self._max_output_bytes = max_output_bytes
        self._runtime_factory = runtime_factory
        self._runtime: object | None = None
        self._model_lock = Lock()
        self._inference_lock = Lock()

    def load(self) -> None:
        with self._model_lock:
            if self._runtime is not None:
                return
            factory = self._runtime_factory or _KokoroRuntime
            self._runtime = factory(*self._paths, self._cpu_threads)

    def synthesize(self, text: str, cancel: Event) -> SynthesizedAudio:
        return _join_chunks(self.synthesize_streaming(text, cancel))

    def synthesize_streaming(
        self,
        text: str,
        cancel: Event,
    ) -> Iterable[SynthesizedAudio]:
        _validate_text(text, self._max_text_chars, "Kokoro")
        if cancel.is_set():
            raise VoiceCancelled()
        self.load()
        while not self._inference_lock.acquire(timeout=0.05):
            if cancel.is_set():
                raise VoiceCancelled()
        try:
            total_bytes = 0
            language = "zh" if _CJK.search(text) else "en"
            for chunk in self._runtime.synthesize(text, language):
                if cancel.is_set():
                    raise VoiceCancelled()
                if not isinstance(chunk, SynthesizedAudio):
                    raise ValueError("Kokoro returned invalid audio")
                total_bytes += len(chunk.data)
                if total_bytes > self._max_output_bytes:
                    raise ValueError("Kokoro output exceeds size limit")
                yield chunk
            if total_bytes == 0:
                raise RuntimeError("Kokoro returned no audio")
        finally:
            self._inference_lock.release()


class _KokoroRuntime:
    def __init__(
        self,
        config_path: str,
        model_path: str,
        chinese_voice_path: str,
        english_voice_path: str,
        cpu_threads: int,
    ) -> None:
        try:
            import numpy
            import torch
            from kokoro import KModel, KPipeline
        except ImportError as error:
            raise RuntimeError("Kokoro runtime is unavailable") from error
        torch.set_num_threads(cpu_threads)
        model = KModel(
            repo_id="hexgrad/Kokoro-82M",
            config=config_path,
            model=model_path,
        ).to("cpu").eval()
        self._numpy = numpy
        self._chinese = KPipeline(
            lang_code="z",
            repo_id="hexgrad/Kokoro-82M",
            model=model,
            device="cpu",
        )
        self._english = KPipeline(
            lang_code="a",
            repo_id="hexgrad/Kokoro-82M",
            model=model,
            device="cpu",
        )
        self._voices = {
            "zh": chinese_voice_path,
            "en": english_voice_path,
        }

    def synthesize(
        self,
        text: str,
        language: str,
    ) -> Iterable[SynthesizedAudio]:
        pipeline = self._chinese if language == "zh" else self._english
        for result in pipeline(text, voice=self._voices[language], speed=1.0):
            audio = getattr(result, "audio", None)
            if audio is None and isinstance(result, tuple) and len(result) >= 3:
                audio = result[2]
            if audio is None:
                raise ValueError("Kokoro returned no waveform")
            values = audio.detach().cpu().numpy() if hasattr(
                audio, "detach"
            ) else self._numpy.asarray(audio)
            pcm = self._numpy.clip(values, -1.0, 1.0)
            pcm = (pcm * 32767.0).astype("<i2", copy=False).tobytes()
            if not pcm:
                raise ValueError("Kokoro returned empty waveform")
            yield SynthesizedAudio(pcm, 24_000)
