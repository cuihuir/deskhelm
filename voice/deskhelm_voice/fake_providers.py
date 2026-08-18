from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from threading import Event, Lock

from .models import CapturedAudio, SynthesizedAudio, Transcript
from .providers import VoiceCancelled


@dataclass(slots=True)
class FakeCaptureProvider:
    captures: Iterable[CapturedAudio]
    started: Event = field(default_factory=Event, init=False)
    requests: int = field(default=0, init=False)
    _captures: deque[CapturedAudio] = field(init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._captures = deque(self.captures)

    def capture(self, stop: Event, cancel: Event) -> CapturedAudio:
        with self._lock:
            if not self._captures:
                raise RuntimeError("fake capture provider has no audio")
            audio = self._captures.popleft()
            self.requests += 1
        self.started.set()
        while not stop.wait(timeout=0.01):
            if cancel.is_set():
                raise VoiceCancelled()
        if cancel.is_set():
            raise VoiceCancelled()
        return audio


@dataclass(slots=True)
class FakeAsrProvider:
    transcripts: Iterable[Transcript]
    requests: list[CapturedAudio] = field(default_factory=list, init=False)
    _transcripts: deque[Transcript] = field(init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._transcripts = deque(self.transcripts)

    def transcribe(self, audio: CapturedAudio, cancel: Event) -> Transcript:
        if cancel.is_set():
            raise VoiceCancelled()
        with self._lock:
            if not self._transcripts:
                raise RuntimeError("fake ASR provider has no transcript")
            self.requests.append(audio)
            return self._transcripts.popleft()


@dataclass(slots=True)
class FakeTtsProvider:
    sample_rate_hz: int = 24000
    requests: list[str] = field(default_factory=list, init=False, repr=False)

    def synthesize(self, text: str, cancel: Event) -> SynthesizedAudio:
        if cancel.is_set():
            raise VoiceCancelled()
        self.requests.append(text)
        return SynthesizedAudio(
            data=text.encode("utf-8"),
            sample_rate_hz=self.sample_rate_hz,
        )


@dataclass(slots=True)
class FakePlaybackProvider:
    block_until_cancel: bool = False
    started: Event = field(default_factory=Event, init=False)
    completed: Event = field(default_factory=Event, init=False)
    requests: list[SynthesizedAudio] = field(default_factory=list, init=False)
    cancelled_count: int = field(default=0, init=False)

    def play(self, audio: SynthesizedAudio, cancel: Event) -> None:
        self.requests.append(audio)
        self.started.set()
        if self.block_until_cancel:
            cancel.wait(timeout=5)
        if cancel.is_set():
            self.cancelled_count += 1
            raise VoiceCancelled()
        self.completed.set()
