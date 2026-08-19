from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import uuid


class PcmSampleFormat(StrEnum):
    S16LE = "s16le"

    @property
    def bytes_per_sample(self) -> int:
        return {PcmSampleFormat.S16LE: 2}[self]


@dataclass(frozen=True, slots=True)
class VoiceTarget:
    agent_id: str
    session_id: str
    project_id: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.agent_id, "agent_id"),
            (self.session_id, "session_id"),
            (self.project_id, "project_id"),
        ):
            _validate_non_empty_string(value, name)


@dataclass(frozen=True, slots=True)
class CapturedAudio:
    data: bytes = field(repr=False)
    sample_rate_hz: int
    channels: int = 1
    sample_format: PcmSampleFormat = PcmSampleFormat.S16LE

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("captured audio data must not be empty")
        _validate_positive_integer(self.sample_rate_hz, "sample_rate_hz")
        _validate_positive_integer(self.channels, "channels")
        _validate_pcm_data(
            self.data,
            self.channels,
            self.sample_format,
            "captured audio",
        )

    @property
    def duration_seconds(self) -> float:
        return len(self.data) / (
            self.sample_rate_hz
            * self.channels
            * self.sample_format.bytes_per_sample
        )


@dataclass(frozen=True, slots=True)
class Transcript:
    raw_text: str = field(repr=False)
    normalized_text: str = field(repr=False)
    transcript_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.raw_text, "raw_text")
        _validate_non_empty_string(self.normalized_text, "normalized_text")
        _validate_non_empty_string(self.transcript_id, "transcript_id")


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    data: bytes = field(repr=False)
    sample_rate_hz: int
    channels: int = 1
    sample_format: PcmSampleFormat = PcmSampleFormat.S16LE

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("synthesized audio data must not be empty")
        _validate_positive_integer(self.sample_rate_hz, "sample_rate_hz")
        _validate_positive_integer(self.channels, "channels")
        _validate_pcm_data(
            self.data,
            self.channels,
            self.sample_format,
            "synthesized audio",
        )

    @property
    def duration_seconds(self) -> float:
        return len(self.data) / (
            self.sample_rate_hz
            * self.channels
            * self.sample_format.bytes_per_sample
        )


class SpeechPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class SpeechItem:
    target: VoiceTarget
    text: str = field(repr=False)
    priority: SpeechPriority = SpeechPriority.NORMAL
    interruptible: bool = True
    speech_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if not isinstance(self.target, VoiceTarget):
            raise ValueError("speech target is invalid")
        _validate_non_empty_string(self.text, "speech text")
        if not isinstance(self.priority, SpeechPriority):
            raise ValueError("speech priority is invalid")
        if not isinstance(self.interruptible, bool):
            raise ValueError("interruptible must be a boolean")
        _validate_non_empty_string(self.speech_id, "speech_id")


class VoicePttState(StrEnum):
    IDLE = "idle"
    CAPTURING = "capturing"
    TRANSCRIBING = "transcribing"


class VoiceEventKind(StrEnum):
    PTT_STARTED = "ptt_started"
    INPUT_SPEECH_STARTED = "input_speech_started"
    INPUT_SPEECH_ENDED = "input_speech_ended"
    INPUT_ACTIVITY_FAILED = "input_activity_failed"
    TRANSCRIBING = "transcribing"
    TRANSCRIPT_READY = "transcript_ready"
    PTT_CANCELLED = "ptt_cancelled"
    SPEECH_STARTED = "speech_started"
    SPEECH_COMPLETED = "speech_completed"
    SPEECH_CANCELLED = "speech_cancelled"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class VoiceEvent:
    kind: VoiceEventKind
    target: VoiceTarget
    speech_id: str = ""
    error_code: str = ""
    audio_frame_index: int | None = None
    transcript: Transcript | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VoiceEventKind):
            raise ValueError("voice event kind is invalid")
        if not isinstance(self.target, VoiceTarget):
            raise ValueError("voice event target is invalid")
        if not isinstance(self.speech_id, str):
            raise ValueError("speech_id must be a string")
        if not isinstance(self.error_code, str):
            raise ValueError("error_code must be a string")
        activity_kinds = {
            VoiceEventKind.INPUT_SPEECH_STARTED,
            VoiceEventKind.INPUT_SPEECH_ENDED,
        }
        if self.kind in activity_kinds:
            if (
                not isinstance(self.audio_frame_index, int)
                or isinstance(self.audio_frame_index, bool)
                or self.audio_frame_index < 0
            ):
                raise ValueError("input speech event requires an audio frame")
        elif self.audio_frame_index is not None:
            raise ValueError("audio_frame_index is only valid for input speech")
        if self.transcript is not None and not isinstance(
            self.transcript, Transcript
        ):
            raise ValueError("voice event transcript is invalid")
        if self.kind in {
            VoiceEventKind.FAILURE,
            VoiceEventKind.INPUT_ACTIVITY_FAILED,
        } and not self.error_code:
            raise ValueError("failure event error_code must not be empty")
        if self.kind is VoiceEventKind.TRANSCRIPT_READY and self.transcript is None:
            raise ValueError("transcript_ready event requires a transcript")
        if self.kind in {
            VoiceEventKind.SPEECH_STARTED,
            VoiceEventKind.SPEECH_COMPLETED,
            VoiceEventKind.SPEECH_CANCELLED,
        } and not self.speech_id:
            raise ValueError("speech event requires speech_id")


def _validate_non_empty_string(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _validate_positive_integer(value: object, name: str) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive integer")


def _validate_pcm_data(
    data: bytes,
    channels: int,
    sample_format: object,
    name: str,
) -> None:
    if not isinstance(sample_format, PcmSampleFormat):
        raise ValueError(f"{name} sample format is invalid")
    frame_bytes = channels * sample_format.bytes_per_sample
    if len(data) % frame_bytes != 0:
        raise ValueError(f"{name} must contain complete PCM frames")
