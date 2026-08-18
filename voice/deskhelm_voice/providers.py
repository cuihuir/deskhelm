from __future__ import annotations

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
