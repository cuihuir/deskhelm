from __future__ import annotations

import math
from threading import Event, Lock
import time
from typing import Callable

from .models import CapturedAudio, PcmSampleFormat, Transcript
from .providers import StreamingAsrResult, VoiceCancelled


class ParaformerStreamingAsrProvider:
    def __init__(
        self,
        model_directory: str,
        *,
        chunk_size: tuple[int, int, int] = (0, 10, 5),
        encoder_chunk_look_back: int = 4,
        decoder_chunk_look_back: int = 1,
        cpu_threads: int = 4,
        max_audio_seconds: float = 120.0,
        model_factory: Callable[[str, int], object] | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if (
            len(chunk_size) != 3
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in chunk_size
            )
            or chunk_size[1] < 1
        ):
            raise ValueError("Paraformer chunk size is invalid")
        if not 0 <= encoder_chunk_look_back <= 100:
            raise ValueError("Paraformer encoder look-back is invalid")
        if not 0 <= decoder_chunk_look_back <= 100:
            raise ValueError("Paraformer decoder look-back is invalid")
        if not 1 <= cpu_threads <= 32:
            raise ValueError("Paraformer CPU thread count is invalid")
        if (
            not isinstance(max_audio_seconds, (int, float))
            or isinstance(max_audio_seconds, bool)
            or not math.isfinite(max_audio_seconds)
            or not 0 < max_audio_seconds <= 3600
        ):
            raise ValueError("Paraformer audio duration limit is invalid")
        self._model_directory = model_directory
        self._chunk_size = chunk_size
        self._encoder_chunk_look_back = encoder_chunk_look_back
        self._decoder_chunk_look_back = decoder_chunk_look_back
        self._cpu_threads = cpu_threads
        self._max_audio_seconds = float(max_audio_seconds)
        self._model_factory = model_factory
        self._monotonic_ns = monotonic_ns
        self._model: object | None = None
        self._model_lock = Lock()
        self._inference_lock = Lock()

    def load(self) -> None:
        with self._model_lock:
            if self._model is not None:
                return
            if self._model_factory is None:
                try:
                    import torch
                    from funasr import AutoModel
                except ImportError as error:
                    raise RuntimeError("FunASR runtime is unavailable") from error
                torch.set_num_threads(self._cpu_threads)
                self._model = AutoModel(
                    model=self._model_directory,
                    device="cpu",
                    disable_update=True,
                )
            else:
                self._model = self._model_factory(
                    self._model_directory,
                    self._cpu_threads,
                )

    def transcribe(
        self,
        audio: CapturedAudio,
        cancel: Event,
    ) -> Transcript:
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
            raise ValueError("Paraformer requires 16 kHz mono S16LE PCM")
        if cancel.is_set():
            raise VoiceCancelled()
        if audio.duration_seconds > self._max_audio_seconds:
            raise ValueError("Paraformer audio exceeds duration limit")
        self.load()
        try:
            import numpy
        except ImportError as error:
            raise RuntimeError("NumPy is unavailable") from error
        samples = numpy.frombuffer(audio.data, dtype="<i2").astype(
            numpy.float32
        ) / 32768.0
        chunk_frames = self._chunk_size[1] * 960
        chunk_count = (len(samples) - 1) // chunk_frames + 1
        cache: dict[str, object] = {}
        parts = []
        first_partial_latency_ms: float | None = None
        while not self._inference_lock.acquire(timeout=0.05):
            if cancel.is_set():
                raise VoiceCancelled()
        try:
            for index in range(chunk_count):
                if cancel.is_set():
                    raise VoiceCancelled()
                chunk = samples[
                    index * chunk_frames : (index + 1) * chunk_frames
                ]
                chunk_start_ns = self._monotonic_ns()
                result = self._model.generate(
                    input=chunk,
                    cache=cache,
                    is_final=index == chunk_count - 1,
                    chunk_size=list(self._chunk_size),
                    encoder_chunk_look_back=self._encoder_chunk_look_back,
                    decoder_chunk_look_back=self._decoder_chunk_look_back,
                    disable_pbar=True,
                )
                text = _extract_text(result)
                if text:
                    parts.append(text)
                    if first_partial_latency_ms is None:
                        chunk_processing_ms = (
                            self._monotonic_ns() - chunk_start_ns
                        ) / 1_000_000
                        available_frames = min(
                            (index + 1) * chunk_frames,
                            len(samples),
                        )
                        first_partial_latency_ms = (
                            available_frames * 1000 / audio.sample_rate_hz
                            + chunk_processing_ms
                        )
        finally:
            self._inference_lock.release()
        raw_text = "".join(parts).strip()
        if not raw_text:
            raise RuntimeError("Paraformer returned no transcript")
        if len(raw_text) > 4096:
            raise RuntimeError("Paraformer transcript exceeds size limit")
        transcript = Transcript(raw_text=raw_text, normalized_text=raw_text)
        return StreamingAsrResult(transcript, first_partial_latency_ms)


def _extract_text(result: object) -> str:
    if (
        not isinstance(result, list)
        or not result
        or not isinstance(result[0], dict)
        or not isinstance(result[0].get("text"), str)
    ):
        raise ValueError("Paraformer returned an invalid result")
    return result[0]["text"]
