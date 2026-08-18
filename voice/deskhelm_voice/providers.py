from __future__ import annotations

from dataclasses import dataclass
import math
from threading import Event
from typing import Protocol, Self

from .models import CapturedAudio, SynthesizedAudio, Transcript
from .streaming import PcmChunk, PcmStreamFormat, VadEvent


class VoiceCancelled(Exception):
    pass


class CaptureProvider(Protocol):
    def capture(self, stop: Event, cancel: Event) -> CapturedAudio: ...


class AsrProvider(Protocol):
    def transcribe(self, audio: CapturedAudio, cancel: Event) -> Transcript: ...


@dataclass(frozen=True, slots=True)
class StreamingAsrResult:
    transcript: Transcript
    first_partial_latency_ms: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.transcript, Transcript):
            raise ValueError("streaming ASR transcript is invalid")
        if self.first_partial_latency_ms is not None and (
            not isinstance(self.first_partial_latency_ms, (int, float))
            or isinstance(self.first_partial_latency_ms, bool)
            or not math.isfinite(self.first_partial_latency_ms)
            or self.first_partial_latency_ms < 0
        ):
            raise ValueError("first partial latency is invalid")


class StreamingAsrProvider(Protocol):
    def transcribe_streaming(
        self,
        audio: CapturedAudio,
        cancel: Event,
    ) -> StreamingAsrResult: ...


class TtsProvider(Protocol):
    def synthesize(self, text: str, cancel: Event) -> SynthesizedAudio: ...


class PlaybackProvider(Protocol):
    def play(self, audio: SynthesizedAudio, cancel: Event) -> None: ...


class PcmChunkStream(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc_value, traceback) -> None: ...

    def read(self, stop: Event, cancel: Event) -> PcmChunk | None: ...


class StreamingCaptureProvider(Protocol):
    def open_stream(self) -> PcmChunkStream: ...


class VadSession(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc_value, traceback) -> None: ...

    def process(self, chunk: PcmChunk, cancel: Event) -> tuple[VadEvent, ...]: ...

    def finish(self, cancel: Event) -> tuple[VadEvent, ...]: ...


class VadProvider(Protocol):
    def open_session(self, format: PcmStreamFormat) -> VadSession: ...
