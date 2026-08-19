from __future__ import annotations

import math
from pathlib import Path
from threading import Event, Lock
from typing import Callable

from .models import CapturedAudio, PcmSampleFormat, Transcript
from .providers import StreamingAsrResult, VoiceCancelled, VoiceNoTranscript


class SenseVoiceOfflineAsrProvider:
    def __init__(
        self,
        model_directory: str,
        *,
        cpu_threads: int = 4,
        language: str = "auto",
        use_itn: bool = True,
        max_audio_seconds: float = 120.0,
        recognizer_factory: Callable[..., object] | None = None,
    ) -> None:
        if not 1 <= cpu_threads <= 32:
            raise ValueError("SenseVoice CPU thread count is invalid")
        if language not in {"auto", "zh", "en", "ja", "ko", "yue"}:
            raise ValueError("SenseVoice language is invalid")
        if (
            not isinstance(max_audio_seconds, (int, float))
            or isinstance(max_audio_seconds, bool)
            or not math.isfinite(max_audio_seconds)
            or not 0 < max_audio_seconds <= 3600
        ):
            raise ValueError("SenseVoice audio duration limit is invalid")
        model_root = Path(model_directory)
        self._model_path = str(model_root / "model.int8.onnx")
        self._tokens_path = str(model_root / "tokens.txt")
        self._cpu_threads = cpu_threads
        self._language = language
        self._use_itn = use_itn
        self._max_audio_seconds = float(max_audio_seconds)
        self._recognizer_factory = recognizer_factory
        self._recognizer: object | None = None
        self._model_lock = Lock()
        self._inference_lock = Lock()

    def load(self) -> None:
        with self._model_lock:
            if self._recognizer is not None:
                return
            if self._recognizer_factory is None:
                try:
                    import sherpa_onnx
                except ImportError as error:
                    raise RuntimeError(
                        "sherpa-onnx runtime is unavailable"
                    ) from error
                factory = sherpa_onnx.OfflineRecognizer.from_sense_voice
            else:
                factory = self._recognizer_factory
            self._recognizer = factory(
                model=self._model_path,
                tokens=self._tokens_path,
                num_threads=self._cpu_threads,
                language=self._language,
                use_itn=self._use_itn,
                debug=False,
                provider="cpu",
            )

    def transcribe(self, audio: CapturedAudio, cancel: Event) -> Transcript:
        return self.transcribe_streaming(audio, cancel).transcript

    def transcribe_streaming(
        self,
        audio: CapturedAudio,
        cancel: Event,
    ) -> StreamingAsrResult:
        if (
            audio.sample_format is not PcmSampleFormat.S16LE
            or audio.sample_rate_hz != 16_000
            or audio.channels != 1
        ):
            raise ValueError("SenseVoice requires 16 kHz mono S16LE PCM")
        if cancel.is_set():
            raise VoiceCancelled()
        if audio.duration_seconds > self._max_audio_seconds:
            raise ValueError("SenseVoice audio exceeds duration limit")
        self.load()
        try:
            import numpy
        except ImportError as error:
            raise RuntimeError("NumPy is unavailable") from error
        samples = numpy.frombuffer(audio.data, dtype="<i2").astype(
            numpy.float32
        ) / 32768.0
        while not self._inference_lock.acquire(timeout=0.05):
            if cancel.is_set():
                raise VoiceCancelled()
        try:
            if cancel.is_set():
                raise VoiceCancelled()
            stream = self._recognizer.create_stream()
            stream.accept_waveform(audio.sample_rate_hz, samples)
            if cancel.is_set():
                raise VoiceCancelled()
            self._recognizer.decode_stream(stream)
            if cancel.is_set():
                raise VoiceCancelled()
            raw_text = _extract_text(stream.result).strip()
        finally:
            self._inference_lock.release()
        if not raw_text:
            raise VoiceNoTranscript()
        if len(raw_text) > 4096:
            raise RuntimeError("SenseVoice transcript exceeds size limit")
        transcript = Transcript(raw_text=raw_text, normalized_text=raw_text)
        return StreamingAsrResult(transcript, None)


def _extract_text(result: object) -> str:
    text = getattr(result, "text", None)
    if not isinstance(text, str):
        raise ValueError("SenseVoice returned an invalid result")
    return text
