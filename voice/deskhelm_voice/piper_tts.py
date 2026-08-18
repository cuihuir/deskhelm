from __future__ import annotations

from collections.abc import Iterable
import json
from pathlib import Path
from threading import Event, Lock
from typing import Callable

from .models import SynthesizedAudio
from .providers import VoiceCancelled


class PiperTtsProvider:
    def __init__(
        self,
        model_path: str,
        config_path: str,
        resource_directory: str,
        *,
        cpu_threads: int = 4,
        max_text_chars: int = 4096,
        max_output_bytes: int = 64 << 20,
        voice_factory: Callable[[str, str], object] | None = None,
    ) -> None:
        if not isinstance(model_path, str) or not model_path:
            raise ValueError("Piper model path is invalid")
        if not isinstance(config_path, str) or not config_path:
            raise ValueError("Piper config path is invalid")
        if not isinstance(resource_directory, str) or not resource_directory:
            raise ValueError("Piper resource directory is invalid")
        if (
            not isinstance(cpu_threads, int)
            or isinstance(cpu_threads, bool)
            or not 1 <= cpu_threads <= 32
        ):
            raise ValueError("Piper CPU thread count is invalid")
        if (
            not isinstance(max_text_chars, int)
            or isinstance(max_text_chars, bool)
            or not 1 <= max_text_chars <= 65_536
        ):
            raise ValueError("Piper text limit is invalid")
        if (
            not isinstance(max_output_bytes, int)
            or isinstance(max_output_bytes, bool)
            or not 2 <= max_output_bytes <= 1 << 30
        ):
            raise ValueError("Piper output limit is invalid")
        self._model_path = model_path
        self._config_path = config_path
        self._resource_directory = resource_directory
        self._cpu_threads = cpu_threads
        self._max_text_chars = max_text_chars
        self._max_output_bytes = max_output_bytes
        self._voice_factory = voice_factory
        self._voice: object | None = None
        self._model_lock = Lock()
        self._inference_lock = Lock()

    def load(self) -> None:
        with self._model_lock:
            if self._voice is not None:
                return
            if self._voice_factory is None:
                try:
                    import onnxruntime
                    from piper import PiperVoice
                    from piper.config import PiperConfig
                    from piper.g2pw_onnx import G2PWOnnxConverter
                    from piper.phonemize_chinese import ChinesePhonemizer
                    from unicode_rbnf import RbnfEngine
                except ImportError as error:
                    raise RuntimeError("Piper runtime is unavailable") from error
                try:
                    config = json.loads(
                        Path(self._config_path).read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    raise ValueError("unable to read Piper config") from error
                session_options = onnxruntime.SessionOptions()
                session_options.intra_op_num_threads = self._cpu_threads
                session_options.inter_op_num_threads = 1
                voice = PiperVoice(
                    session=onnxruntime.InferenceSession(
                        self._model_path,
                        sess_options=session_options,
                        providers=["CPUExecutionProvider"],
                    ),
                    config=PiperConfig.from_dict(config),
                    download_dir=Path(self._resource_directory),
                )
                g2pw_directory = Path(self._resource_directory) / "g2pW"
                tokenizer_directory = Path(self._resource_directory)
                required = (
                    g2pw_directory / "g2pw.onnx",
                    tokenizer_directory / "tokenizer.json",
                    tokenizer_directory / "tokenizer_config.json",
                    tokenizer_directory / "vocab.txt",
                )
                if not all(path.is_file() for path in required):
                    raise ValueError("Piper Chinese resources are incomplete")
                phonemizer = ChinesePhonemizer.__new__(ChinesePhonemizer)
                phonemizer.g2p = G2PWOnnxConverter(
                    model_dir=g2pw_directory,
                    style="pinyin",
                    model_source=str(tokenizer_directory),
                    enable_non_tradional_chinese=True,
                )
                phonemizer.number_engine = RbnfEngine.for_language("zh")
                setattr(voice, "_chinese_phonemizer", phonemizer)
                self._voice = voice
            else:
                self._voice = self._voice_factory(
                    self._model_path,
                    self._config_path,
                    self._resource_directory,
                    self._cpu_threads,
                )

    def synthesize(self, text: str, cancel: Event) -> SynthesizedAudio:
        return _join_chunks(self.synthesize_streaming(text, cancel))

    def synthesize_streaming(
        self,
        text: str,
        cancel: Event,
    ) -> Iterable[SynthesizedAudio]:
        _validate_text(text, self._max_text_chars, "Piper")
        if cancel.is_set():
            raise VoiceCancelled()
        self.load()
        while not self._inference_lock.acquire(timeout=0.05):
            if cancel.is_set():
                raise VoiceCancelled()
        try:
            total_bytes = 0
            for chunk in self._voice.synthesize(text):
                if cancel.is_set():
                    raise VoiceCancelled()
                if (
                    getattr(chunk, "sample_width", None) != 2
                    or getattr(chunk, "sample_channels", None) != 1
                ):
                    raise ValueError("Piper returned an unsupported audio format")
                data = getattr(chunk, "audio_int16_bytes", None)
                sample_rate = getattr(chunk, "sample_rate", None)
                if not isinstance(data, bytes) or not data:
                    raise ValueError("Piper returned invalid audio")
                total_bytes += len(data)
                if total_bytes > self._max_output_bytes:
                    raise ValueError("Piper output exceeds size limit")
                yield SynthesizedAudio(data, sample_rate)
            if total_bytes == 0:
                raise RuntimeError("Piper returned no audio")
        finally:
            self._inference_lock.release()


def _validate_text(text: str, limit: int, provider: str) -> None:
    if not isinstance(text, str) or not text.strip() or len(text) > limit:
        raise ValueError(f"{provider} text is invalid")


def _join_chunks(chunks: Iterable[SynthesizedAudio]) -> SynthesizedAudio:
    collected = tuple(chunks)
    if not collected:
        raise RuntimeError("TTS returned no audio")
    first = collected[0]
    if any(
        chunk.sample_rate_hz != first.sample_rate_hz
        or chunk.channels != first.channels
        or chunk.sample_format is not first.sample_format
        for chunk in collected[1:]
    ):
        raise ValueError("TTS output format changed")
    return SynthesizedAudio(
        b"".join(chunk.data for chunk in collected),
        first.sample_rate_hz,
        first.channels,
        first.sample_format,
    )
