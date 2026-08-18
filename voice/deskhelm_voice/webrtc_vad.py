from __future__ import annotations

from collections import deque
from threading import Event
from typing import Callable

from .models import PcmSampleFormat
from .providers import VoiceCancelled
from .streaming import PcmChunk, PcmStreamFormat, VadEvent, VadEventKind


class WebRtcVadProvider:
    def __init__(
        self,
        *,
        mode: int = 2,
        frame_ms: int = 20,
        start_window_frames: int = 5,
        start_trigger_frames: int = 3,
        end_window_frames: int = 10,
        end_trigger_frames: int = 8,
        vad_factory: Callable[[int], object] | None = None,
    ) -> None:
        if mode not in range(4) or frame_ms not in (10, 20, 30):
            raise ValueError("WebRTC VAD configuration is invalid")
        if not 1 <= start_trigger_frames <= start_window_frames <= 100:
            raise ValueError("WebRTC VAD start window is invalid")
        if not 1 <= end_trigger_frames <= end_window_frames <= 100:
            raise ValueError("WebRTC VAD end window is invalid")
        self._config = (
            mode,
            frame_ms,
            start_window_frames,
            start_trigger_frames,
            end_window_frames,
            end_trigger_frames,
        )
        self._vad_factory = vad_factory

    def open_session(self, format: PcmStreamFormat) -> _WebRtcVadSession:
        if (
            format.sample_format is not PcmSampleFormat.S16LE
            or format.channels != 1
            or format.sample_rate_hz not in (8_000, 16_000, 32_000, 48_000)
        ):
            raise ValueError("WebRTC VAD requires mono S16LE at a supported rate")
        factory = self._vad_factory
        if factory is None:
            try:
                import webrtcvad
            except ImportError as error:
                raise RuntimeError("WebRTC VAD runtime is unavailable") from error
            factory = webrtcvad.Vad
        return _WebRtcVadSession(format, factory, *self._config)


class _WebRtcVadSession:
    def __init__(
        self,
        format,
        factory,
        mode,
        frame_ms,
        start_window,
        start_trigger,
        end_window,
        end_trigger,
    ):
        self._format = format
        self._detector = factory(mode)
        self._frame_count = format.sample_rate_hz * frame_ms // 1000
        self._frame_bytes = self._frame_count * format.frame_bytes
        self._start_flags = deque(maxlen=start_window)
        self._end_flags = deque(maxlen=end_window)
        self._start_trigger = start_trigger
        self._end_trigger = end_trigger
        self._buffer = bytearray()
        self._buffer_start = 0
        self._expected_frame = 0
        self._active_start: int | None = None
        self._first_unvoiced: int | None = None
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._closed = True

    def process(self, chunk: PcmChunk, cancel: Event) -> tuple[VadEvent, ...]:
        if (
            self._closed
            or chunk.format != self._format
            or chunk.start_frame != self._expected_frame
        ):
            raise ValueError("WebRTC VAD chunk sequence is invalid")
        if cancel.is_set():
            raise VoiceCancelled()
        if not self._buffer:
            self._buffer_start = chunk.start_frame
        self._buffer.extend(chunk.data)
        self._expected_frame = chunk.end_frame
        events = []
        while len(self._buffer) >= self._frame_bytes:
            frame = bytes(self._buffer[: self._frame_bytes])
            del self._buffer[: self._frame_bytes]
            events.extend(self._process_frame(frame, self._buffer_start))
            self._buffer_start += self._frame_count
        return tuple(events)

    def _process_frame(self, pcm: bytes, start_frame: int) -> list[VadEvent]:
        voiced = bool(
            self._detector.is_speech(pcm, self._format.sample_rate_hz)
        )
        if self._active_start is None:
            self._start_flags.append((start_frame, voiced))
            if sum(flag for _, flag in self._start_flags) >= self._start_trigger:
                start = next(frame for frame, flag in self._start_flags if flag)
                self._active_start = start
                self._end_flags.clear()
                return [VadEvent(VadEventKind.SPEECH_STARTED, start)]
            return []
        self._end_flags.append((start_frame, not voiced))
        if not voiced and self._first_unvoiced is None:
            self._first_unvoiced = start_frame
        elif voiced:
            self._first_unvoiced = None
        if sum(flag for _, flag in self._end_flags) >= self._end_trigger:
            end = (
                self._first_unvoiced
                if self._first_unvoiced is not None
                else start_frame
            )
            end = max(end, self._active_start + 1)
            self._active_start = None
            self._first_unvoiced = None
            self._start_flags.clear()
            return [VadEvent(VadEventKind.SPEECH_ENDED, end)]
        return []

    def finish(self, cancel: Event) -> tuple[VadEvent, ...]:
        if self._closed:
            raise ValueError("WebRTC VAD session is closed")
        if cancel.is_set():
            raise VoiceCancelled()
        if self._active_start is None:
            return ()
        end = max(self._expected_frame, self._active_start + 1)
        self._active_start = None
        return (VadEvent(VadEventKind.SPEECH_ENDED, end),)
