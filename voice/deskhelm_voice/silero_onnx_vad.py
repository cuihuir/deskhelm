from __future__ import annotations

from threading import Event, Lock
from typing import Callable

from .models import PcmSampleFormat
from .providers import VoiceCancelled
from .streaming import PcmChunk, PcmStreamFormat, VadEvent, VadEventKind


class SileroOnnxVadProvider:
    def __init__(
        self,
        model_path: str,
        *,
        threshold: float = 0.5,
        negative_threshold: float = 0.35,
        minimum_silence_ms: int = 100,
        speech_pad_ms: int = 30,
        session_factory: Callable[[str], object] | None = None,
    ) -> None:
        if not 0 < negative_threshold < threshold < 1:
            raise ValueError("Silero thresholds are invalid")
        if not 0 <= speech_pad_ms <= minimum_silence_ms <= 10_000:
            raise ValueError("Silero timing configuration is invalid")
        self._model_path = model_path
        self._threshold = threshold
        self._negative_threshold = negative_threshold
        self._minimum_silence_ms = minimum_silence_ms
        self._speech_pad_ms = speech_pad_ms
        self._session_factory = session_factory
        self._runtime_session: object | None = None
        self._runtime_lock = Lock()

    def open_session(self, format: PcmStreamFormat) -> _SileroOnnxVadSession:
        if (
            format.sample_format is not PcmSampleFormat.S16LE
            or format.channels != 1
            or format.sample_rate_hz != 16_000
        ):
            raise ValueError("Silero VAD requires 16 kHz mono S16LE PCM")
        try:
            import numpy
        except ImportError as error:
            raise RuntimeError("NumPy is unavailable") from error
        with self._runtime_lock:
            if self._runtime_session is None:
                if self._session_factory is None:
                    try:
                        import onnxruntime
                    except ImportError as error:
                        raise RuntimeError(
                            "ONNX Runtime is unavailable"
                        ) from error
                    options = onnxruntime.SessionOptions()
                    options.intra_op_num_threads = 1
                    options.inter_op_num_threads = 1
                    options.execution_mode = (
                        onnxruntime.ExecutionMode.ORT_SEQUENTIAL
                    )
                    self._runtime_session = onnxruntime.InferenceSession(
                        self._model_path,
                        sess_options=options,
                        providers=["CPUExecutionProvider"],
                    )
                else:
                    self._runtime_session = self._session_factory(
                        self._model_path
                    )
        return _SileroOnnxVadSession(
            format,
            self._runtime_session,
            numpy,
            self._threshold,
            self._negative_threshold,
            self._minimum_silence_ms,
            self._speech_pad_ms,
        )


class _SileroOnnxVadSession:
    def __init__(
        self,
        format,
        session,
        numpy,
        threshold,
        negative_threshold,
        minimum_silence_ms,
        speech_pad_ms,
    ):
        self._format = format
        self._session = session
        self._np = numpy
        self._threshold = threshold
        self._negative_threshold = negative_threshold
        self._minimum_silence_frames = (
            format.sample_rate_hz * minimum_silence_ms // 1000
        )
        self._pad_frames = format.sample_rate_hz * speech_pad_ms // 1000
        self._window_frames = 512
        self._window_bytes = self._window_frames * 2
        self._buffer = bytearray()
        self._buffer_start = 0
        self._expected_frame = 0
        self._state = numpy.zeros((2, 1, 128), dtype=numpy.float32)
        self._context = numpy.zeros((1, 64), dtype=numpy.float32)
        self._sample_rate = numpy.array(format.sample_rate_hz, dtype=numpy.int64)
        self._active_start: int | None = None
        self._candidate_end: int | None = None
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
            raise ValueError("Silero VAD chunk sequence is invalid")
        if cancel.is_set():
            raise VoiceCancelled()
        if not self._buffer:
            self._buffer_start = chunk.start_frame
        self._buffer.extend(chunk.data)
        self._expected_frame = chunk.end_frame
        events = []
        while len(self._buffer) >= self._window_bytes:
            window = bytes(self._buffer[: self._window_bytes])
            del self._buffer[: self._window_bytes]
            events.extend(self._process_window(window, self._buffer_start))
            self._buffer_start += self._window_frames
        return tuple(events)

    def finish(self, cancel: Event) -> tuple[VadEvent, ...]:
        if self._closed:
            raise ValueError("Silero VAD session is closed")
        if cancel.is_set():
            raise VoiceCancelled()
        events = []
        if self._buffer:
            padded = bytes(self._buffer) + bytes(
                self._window_bytes - len(self._buffer)
            )
            events.extend(self._process_window(padded, self._buffer_start))
            self._buffer.clear()
        if self._active_start is not None:
            end = max(self._expected_frame, self._active_start + 1)
            events.append(VadEvent(VadEventKind.SPEECH_ENDED, end))
            self._active_start = None
        return tuple(events)

    def _process_window(self, pcm: bytes, start_frame: int) -> list[VadEvent]:
        samples = self._np.frombuffer(pcm, dtype=self._np.int16).astype(
            self._np.float32
        ) / 32768.0
        model_input = self._np.concatenate(
            (self._context, samples.reshape(1, -1)), axis=1
        )
        output, self._state = self._session.run(
            ["output", "stateN"],
            {"input": model_input, "state": self._state, "sr": self._sample_rate},
        )
        self._context = model_input[:, -64:]
        probability = float(output[0][0])
        if self._active_start is None and probability >= self._threshold:
            start = max(0, start_frame - self._pad_frames)
            self._active_start = start
            self._candidate_end = None
            return [VadEvent(VadEventKind.SPEECH_STARTED, start)]
        if self._active_start is None:
            return []
        if probability < self._negative_threshold:
            if self._candidate_end is None:
                self._candidate_end = start_frame
            silent_frames = (
                start_frame + self._window_frames - self._candidate_end
            )
            if silent_frames >= self._minimum_silence_frames:
                end = min(
                    self._expected_frame,
                    self._candidate_end + self._pad_frames,
                )
                end = max(end, self._active_start + 1)
                self._active_start = None
                self._candidate_end = None
                return [VadEvent(VadEventKind.SPEECH_ENDED, end)]
        else:
            self._candidate_end = None
        return []
