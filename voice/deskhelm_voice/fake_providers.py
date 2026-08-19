from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from threading import Event, Lock

from .models import CapturedAudio, SynthesizedAudio, Transcript
from .providers import VoiceCancelled
from .streaming import PcmChunk, PcmStreamFormat, VadEvent


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
class FakeStreamingCaptureProvider:
    chunks: Iterable[PcmChunk]
    end_on_exhaustion: bool = False
    started: Event = field(default_factory=Event, init=False)
    streams_opened: int = field(default=0, init=False)
    chunks_read: int = field(default=0, init=False)
    streams_closed: int = field(default=0, init=False)
    _chunks: tuple[PcmChunk, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.end_on_exhaustion, bool):
            raise ValueError("fake stream end_on_exhaustion must be boolean")
        self._chunks = tuple(self.chunks)

    def open_stream(self) -> FakePcmChunkStream:
        self.streams_opened += 1
        return FakePcmChunkStream(
            self,
            deque(self._chunks),
            self.end_on_exhaustion,
        )


@dataclass(slots=True)
class FakePcmChunkStream:
    provider: FakeStreamingCaptureProvider = field(repr=False)
    chunks: deque[PcmChunk] = field(repr=False)
    end_on_exhaustion: bool = False
    _open: bool = field(default=False, init=False, repr=False)

    def __enter__(self) -> FakePcmChunkStream:
        if self._open:
            raise RuntimeError("fake PCM stream is already open")
        self._open = True
        self.provider.started.set()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._open:
            self.provider.streams_closed += 1
        self._open = False

    def read(self, stop: Event, cancel: Event) -> PcmChunk | None:
        if not self._open:
            raise RuntimeError("fake PCM stream is not open")
        if cancel.is_set():
            raise VoiceCancelled()
        if self.chunks:
            self.provider.chunks_read += 1
            return self.chunks.popleft()
        if self.end_on_exhaustion:
            return None
        while not stop.wait(timeout=0.01):
            if cancel.is_set():
                raise VoiceCancelled()
        if cancel.is_set():
            raise VoiceCancelled()
        return None


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
        frame_count = max(1, len(text.encode("utf-8")))
        return SynthesizedAudio(
            data=b"\x00\x00" * frame_count,
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


@dataclass(slots=True)
class FakeVadProvider:
    events: Iterable[VadEvent]
    fail: bool = False
    sessions_opened: int = field(default=0, init=False)
    _events: tuple[VadEvent, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._events = tuple(self.events)

    def open_session(self, format: PcmStreamFormat) -> FakeVadSession:
        if not isinstance(format, PcmStreamFormat):
            raise ValueError("fake VAD stream format is invalid")
        self.sessions_opened += 1
        return FakeVadSession(format, self._events, self.fail)


@dataclass(slots=True)
class FakeVadSession:
    format: PcmStreamFormat
    events: tuple[VadEvent, ...]
    fail: bool = False
    _next_event: int = field(default=0, init=False, repr=False)
    _end_frame: int = field(default=0, init=False, repr=False)

    def __enter__(self) -> FakeVadSession:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def process(self, chunk: PcmChunk, cancel: Event) -> tuple[VadEvent, ...]:
        if cancel.is_set():
            raise VoiceCancelled()
        if self.fail:
            raise RuntimeError("private fake VAD failure")
        if chunk.format != self.format or chunk.start_frame != self._end_frame:
            raise ValueError("fake VAD received a discontinuous PCM stream")
        self._end_frame = chunk.end_frame
        emitted = []
        while (
            self._next_event < len(self.events)
            and self.events[self._next_event].frame_index <= chunk.end_frame
        ):
            emitted.append(self.events[self._next_event])
            self._next_event += 1
        return tuple(emitted)

    def finish(self, cancel: Event) -> tuple[VadEvent, ...]:
        if cancel.is_set():
            raise VoiceCancelled()
        if self.fail:
            raise RuntimeError("private fake VAD failure")
        remaining = self.events[self._next_event :]
        if any(event.frame_index > self._end_frame for event in remaining):
            raise ValueError("fake VAD event exceeds the PCM stream")
        self._next_event = len(self.events)
        return remaining
