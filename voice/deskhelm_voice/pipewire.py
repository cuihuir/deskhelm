from __future__ import annotations

from dataclasses import dataclass, field
import os
import select
import signal
import subprocess
from threading import Event
import time
from typing import Self

from .models import CapturedAudio, PcmSampleFormat, SynthesizedAudio
from .providers import VoiceCancelled
from .streaming import PcmChunk, PcmStreamFormat


DEFAULT_CAPTURE_RATE_HZ = 16_000
DEFAULT_CAPTURE_CHANNELS = 1
DEFAULT_MAX_CAPTURE_SECONDS = 30.0
DEFAULT_MAX_CAPTURE_BYTES = 1 << 20
DEFAULT_MAX_PLAYBACK_SECONDS = 120.0
DEFAULT_MAX_PLAYBACK_BYTES = 16 << 20
DEFAULT_POLL_SECONDS = 0.02
DEFAULT_TERMINATE_GRACE_SECONDS = 1.0
IO_CHUNK_BYTES = 65_536


@dataclass(slots=True)
class PipeWireCaptureProvider:
    command_prefix: tuple[str, ...] = ("pw-cat",)
    source_name: str | None = None
    sample_rate_hz: int = DEFAULT_CAPTURE_RATE_HZ
    channels: int = DEFAULT_CAPTURE_CHANNELS
    sample_format: PcmSampleFormat = PcmSampleFormat.S16LE
    latency: str = "20ms"
    max_capture_seconds: float = DEFAULT_MAX_CAPTURE_SECONDS
    max_capture_bytes: int = DEFAULT_MAX_CAPTURE_BYTES
    poll_seconds: float = DEFAULT_POLL_SECONDS
    terminate_grace_seconds: float = DEFAULT_TERMINATE_GRACE_SECONDS

    def __post_init__(self) -> None:
        _validate_common(
            self.command_prefix,
            self.source_name,
            self.sample_format,
            self.poll_seconds,
            self.terminate_grace_seconds,
        )
        _validate_positive_integer(self.sample_rate_hz, "sample_rate_hz")
        _validate_positive_integer(self.channels, "channels")
        _validate_non_empty_string(self.latency, "latency")
        _validate_positive_number(
            self.max_capture_seconds, "max_capture_seconds"
        )
        _validate_positive_integer(self.max_capture_bytes, "max_capture_bytes")

    def capture(self, stop: Event, cancel: Event) -> CapturedAudio:
        if not isinstance(stop, Event) or not isinstance(cancel, Event):
            raise ValueError("capture stop and cancel signals are invalid")
        if cancel.is_set():
            raise VoiceCancelled()
        captured = bytearray()
        with self.open_stream() as stream:
            while True:
                chunk = stream.read(stop, cancel)
                if chunk is None:
                    break
                captured.extend(chunk.data)
        if not captured:
            raise RuntimeError("PipeWire capture produced no audio")
        return CapturedAudio(
            data=bytes(captured),
            sample_rate_hz=self.sample_rate_hz,
            channels=self.channels,
            sample_format=self.sample_format,
        )

    def open_stream(self) -> PipeWirePcmChunkStream:
        return PipeWirePcmChunkStream(self)

    def command(self) -> tuple[str, ...]:
        command = [
            *self.command_prefix,
            "--record",
            "--raw",
            "--rate",
            str(self.sample_rate_hz),
            "--channels",
            str(self.channels),
            "--format",
            _pw_cat_format(self.sample_format),
            "--latency",
            self.latency,
        ]
        if self.source_name is not None:
            command.extend(("--target", self.source_name))
        command.append("-")
        return tuple(command)

    def _start(self) -> subprocess.Popen[bytes]:
        try:
            return subprocess.Popen(
                self.command(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                bufsize=0,
            )
        except OSError as error:
            raise RuntimeError("PipeWire capture could not start") from error


@dataclass(slots=True)
class PipeWirePcmChunkStream:
    provider: PipeWireCaptureProvider = field(repr=False)
    _process: subprocess.Popen[bytes] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _descriptor: int = field(default=-1, init=False, repr=False)
    _deadline: float = field(default=0.0, init=False, repr=False)
    _stop_requested: bool = field(default=False, init=False, repr=False)
    _stop_deadline: float | None = field(default=None, init=False, repr=False)
    _pending: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _bytes_received: int = field(default=0, init=False, repr=False)
    _next_frame: int = field(default=0, init=False, repr=False)
    _finished: bool = field(default=False, init=False, repr=False)

    def __enter__(self) -> Self:
        if self._process is not None:
            raise RuntimeError("PipeWire capture stream is already open")
        process = self.provider._start()
        assert process.stdout is not None
        self._process = process
        self._descriptor = process.stdout.fileno()
        os.set_blocking(self._descriptor, False)
        self._deadline = time.monotonic() + self.provider.max_capture_seconds
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                _terminate_process(
                    process,
                    self.provider.terminate_grace_seconds,
                )
        finally:
            if process.stdout is not None and not process.stdout.closed:
                process.stdout.close()
            self._finished = True

    def read(self, stop: Event, cancel: Event) -> PcmChunk | None:
        if not isinstance(stop, Event) or not isinstance(cancel, Event):
            raise ValueError("capture stop and cancel signals are invalid")
        process = self._process
        if process is None:
            raise RuntimeError("PipeWire capture stream is not open")
        if self._finished:
            return None

        while True:
            if cancel.is_set():
                _terminate_process(
                    process,
                    self.provider.terminate_grace_seconds,
                )
                raise VoiceCancelled()
            now = time.monotonic()
            if now >= self._deadline:
                _terminate_process(
                    process,
                    self.provider.terminate_grace_seconds,
                )
                raise RuntimeError("PipeWire capture duration limit exceeded")
            self._request_stop(process, stop, now)

            readable, _, _ = select.select(
                [process.stdout],
                [],
                [],
                self.provider.poll_seconds,
            )
            if readable:
                data = _read_available(self._descriptor)
                if data:
                    self._append(data, process)
                    chunk = self._take_complete_chunk()
                    if chunk is not None:
                        return chunk
                elif data == b"":
                    return self._finish_process(process)

            if process.poll() is not None:
                drained = _drain_available(self._descriptor)
                if drained:
                    self._append(drained, process)
                return self._finish_process(process)

    def _request_stop(
        self,
        process: subprocess.Popen[bytes],
        stop: Event,
        now: float,
    ) -> None:
        if stop.is_set() and not self._stop_requested:
            self._stop_requested = True
            self._stop_deadline = now + self.provider.terminate_grace_seconds
            _signal_process(process, signal.SIGTERM)
        if (
            self._stop_deadline is not None
            and now >= self._stop_deadline
            and process.poll() is None
        ):
            _signal_process(process, signal.SIGKILL)

    def _append(
        self,
        data: bytes,
        process: subprocess.Popen[bytes],
    ) -> None:
        self._bytes_received += len(data)
        if self._bytes_received > self.provider.max_capture_bytes:
            _terminate_process(
                process,
                self.provider.terminate_grace_seconds,
            )
            raise RuntimeError("PipeWire capture byte limit exceeded")
        self._pending.extend(data)

    def _take_complete_chunk(self) -> PcmChunk | None:
        format = self._format()
        complete_bytes = (
            len(self._pending) // format.frame_bytes * format.frame_bytes
        )
        if complete_bytes == 0:
            return None
        data = bytes(self._pending[:complete_bytes])
        del self._pending[:complete_bytes]
        chunk = PcmChunk(data, format, self._next_frame)
        self._next_frame = chunk.end_frame
        return chunk

    def _finish_process(
        self,
        process: subprocess.Popen[bytes],
    ) -> PcmChunk | None:
        try:
            return_code = process.wait(
                timeout=self.provider.terminate_grace_seconds
            )
        except subprocess.TimeoutExpired:
            _terminate_process(
                process,
                self.provider.terminate_grace_seconds,
            )
            raise RuntimeError("PipeWire capture process failed") from None
        if return_code != 0 and not self._stop_requested:
            raise RuntimeError("PipeWire capture process failed")
        format = self._format()
        if len(self._pending) % format.frame_bytes:
            raise RuntimeError("PipeWire capture produced invalid PCM frames")
        self._finished = True
        return self._take_complete_chunk()

    def _format(self) -> PcmStreamFormat:
        return PcmStreamFormat(
            self.provider.sample_rate_hz,
            self.provider.channels,
            self.provider.sample_format,
        )


@dataclass(slots=True)
class PipeWirePlaybackProvider:
    command_prefix: tuple[str, ...] = ("pw-cat",)
    sink_name: str | None = None
    latency: str = "20ms"
    max_playback_seconds: float = DEFAULT_MAX_PLAYBACK_SECONDS
    max_playback_bytes: int = DEFAULT_MAX_PLAYBACK_BYTES
    poll_seconds: float = DEFAULT_POLL_SECONDS
    terminate_grace_seconds: float = DEFAULT_TERMINATE_GRACE_SECONDS

    def __post_init__(self) -> None:
        _validate_common(
            self.command_prefix,
            self.sink_name,
            PcmSampleFormat.S16LE,
            self.poll_seconds,
            self.terminate_grace_seconds,
        )
        _validate_non_empty_string(self.latency, "latency")
        _validate_positive_number(
            self.max_playback_seconds, "max_playback_seconds"
        )
        _validate_positive_integer(
            self.max_playback_bytes, "max_playback_bytes"
        )

    def play(self, audio: SynthesizedAudio, cancel: Event) -> None:
        if not isinstance(audio, SynthesizedAudio):
            raise ValueError("playback audio is invalid")
        if not isinstance(cancel, Event):
            raise ValueError("playback cancel signal is invalid")
        if cancel.is_set():
            raise VoiceCancelled()
        if len(audio.data) > self.max_playback_bytes:
            raise RuntimeError("PipeWire playback byte limit exceeded")
        if audio.duration_seconds > self.max_playback_seconds:
            raise RuntimeError("PipeWire playback duration limit exceeded")
        process = self._start(audio)
        assert process.stdin is not None
        descriptor = process.stdin.fileno()
        os.set_blocking(descriptor, False)
        deadline = time.monotonic() + self.max_playback_seconds
        offset = 0
        try:
            while offset < len(audio.data):
                if cancel.is_set():
                    _terminate_process(process, self.terminate_grace_seconds)
                    raise VoiceCancelled()
                if time.monotonic() >= deadline:
                    _terminate_process(process, self.terminate_grace_seconds)
                    raise RuntimeError("PipeWire playback timed out")
                if process.poll() is not None:
                    break
                _, writable, _ = select.select(
                    [], [process.stdin], [], self.poll_seconds
                )
                if not writable:
                    continue
                try:
                    written = os.write(descriptor, audio.data[offset:])
                except BrokenPipeError:
                    break
                except BlockingIOError:
                    continue
                if written <= 0:
                    break
                offset += written

            process.stdin.close()
            if offset != len(audio.data):
                _terminate_process(process, self.terminate_grace_seconds)
                raise RuntimeError("PipeWire playback input failed")
            while process.poll() is None:
                if cancel.is_set():
                    _terminate_process(process, self.terminate_grace_seconds)
                    raise VoiceCancelled()
                if time.monotonic() >= deadline:
                    _terminate_process(process, self.terminate_grace_seconds)
                    raise RuntimeError("PipeWire playback timed out")
                time.sleep(self.poll_seconds)
            if process.returncode != 0:
                raise RuntimeError("PipeWire playback process failed")
        finally:
            if not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                _terminate_process(process, self.terminate_grace_seconds)

    def command(self, audio: SynthesizedAudio) -> tuple[str, ...]:
        command = [
            *self.command_prefix,
            "--playback",
            "--raw",
            "--rate",
            str(audio.sample_rate_hz),
            "--channels",
            str(audio.channels),
            "--format",
            _pw_cat_format(audio.sample_format),
            "--latency",
            self.latency,
        ]
        if self.sink_name is not None:
            command.extend(("--target", self.sink_name))
        command.append("-")
        return tuple(command)

    def _start(self, audio: SynthesizedAudio) -> subprocess.Popen[bytes]:
        try:
            return subprocess.Popen(
                self.command(audio),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                bufsize=0,
            )
        except OSError as error:
            raise RuntimeError("PipeWire playback could not start") from error


def _validate_common(
    command_prefix: tuple[str, ...],
    target_name: str | None,
    sample_format: object,
    poll_seconds: float,
    terminate_grace_seconds: float,
) -> None:
    if not command_prefix or not all(
        isinstance(part, str) and part for part in command_prefix
    ):
        raise ValueError("command_prefix must not be empty")
    if target_name is not None:
        _validate_non_empty_string(target_name, "PipeWire target name")
        if target_name.strip().isdigit():
            raise ValueError("PipeWire target must use a stable node name")
    if not isinstance(sample_format, PcmSampleFormat):
        raise ValueError("PCM sample format is invalid")
    _validate_positive_number(poll_seconds, "poll_seconds")
    _validate_positive_number(
        terminate_grace_seconds, "terminate_grace_seconds"
    )


def _pw_cat_format(sample_format: PcmSampleFormat) -> str:
    return {PcmSampleFormat.S16LE: "s16"}[sample_format]


def _read_available(descriptor: int) -> bytes | None:
    try:
        return os.read(descriptor, IO_CHUNK_BYTES)
    except BlockingIOError:
        return None


def _drain_available(descriptor: int) -> bytes:
    drained = bytearray()
    while True:
        chunk = _read_available(descriptor)
        if not chunk:
            break
        drained.extend(chunk)
    return bytes(drained)


def _signal_process(process: subprocess.Popen[bytes], signal_number: int) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal_number)
    except (ProcessLookupError, PermissionError):
        pass


def _terminate_process(
    process: subprocess.Popen[bytes], grace_seconds: float
) -> None:
    if process.poll() is not None:
        return
    _signal_process(process, signal.SIGTERM)
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        _signal_process(process, signal.SIGKILL)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("PipeWire process could not be terminated") from error


def _validate_non_empty_string(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _validate_positive_integer(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _validate_positive_number(value: object, name: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value <= 0
    ):
        raise ValueError(f"{name} must be greater than zero")
