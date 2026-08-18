from __future__ import annotations

from threading import Event
from typing import Protocol

from .models import CapturedAudio, SynthesizedAudio, Transcript


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
