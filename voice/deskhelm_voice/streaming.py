from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .models import PcmSampleFormat


MAX_PCM_CHUNK_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class PcmStreamFormat:
    sample_rate_hz: int
    channels: int = 1
    sample_format: PcmSampleFormat = PcmSampleFormat.S16LE

    def __post_init__(self) -> None:
        _validate_positive_integer(self.sample_rate_hz, "sample_rate_hz")
        _validate_positive_integer(self.channels, "channels")
        if not isinstance(self.sample_format, PcmSampleFormat):
            raise ValueError("PCM stream sample format is invalid")

    @property
    def frame_bytes(self) -> int:
        return self.channels * self.sample_format.bytes_per_sample


@dataclass(frozen=True, slots=True)
class PcmChunk:
    data: bytes = field(repr=False)
    format: PcmStreamFormat
    start_frame: int

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("PCM chunk data must not be empty")
        if len(self.data) > MAX_PCM_CHUNK_BYTES:
            raise ValueError("PCM chunk exceeds size limit")
        if not isinstance(self.format, PcmStreamFormat):
            raise ValueError("PCM chunk format is invalid")
        _validate_non_negative_integer(self.start_frame, "start_frame")
        if len(self.data) % self.format.frame_bytes:
            raise ValueError("PCM chunk must contain complete frames")

    @property
    def frame_count(self) -> int:
        return len(self.data) // self.format.frame_bytes

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.format.sample_rate_hz


class VadEventKind(StrEnum):
    SPEECH_STARTED = "speech_started"
    SPEECH_ENDED = "speech_ended"


@dataclass(frozen=True, slots=True)
class VadEvent:
    kind: VadEventKind
    frame_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VadEventKind):
            raise ValueError("VAD event kind is invalid")
        _validate_non_negative_integer(self.frame_index, "frame_index")


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    start_frame: int
    end_frame: int

    def __post_init__(self) -> None:
        _validate_non_negative_integer(self.start_frame, "start_frame")
        _validate_non_negative_integer(self.end_frame, "end_frame")
        if self.end_frame <= self.start_frame:
            raise ValueError("speech segment end must follow start")

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame


def _validate_positive_integer(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _validate_non_negative_integer(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
