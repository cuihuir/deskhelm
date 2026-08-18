from __future__ import annotations

from dataclasses import dataclass
import os
import select
import signal
import subprocess
from threading import Event
import time

from .models import CapturedAudio, PcmSampleFormat, SynthesizedAudio
from .providers import VoiceCancelled


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
        process = self._start()
        assert process.stdout is not None
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
        deadline = time.monotonic() + self.max_capture_seconds
        stop_requested = False
        stop_deadline: float | None = None
        captured = bytearray()
        try:
            while True:
                if cancel.is_set():
                    _terminate_process(process, self.terminate_grace_seconds)
                    raise VoiceCancelled()
                now = time.monotonic()
                if now >= deadline:
                    _terminate_process(process, self.terminate_grace_seconds)
                    raise RuntimeError("PipeWire capture duration limit exceeded")
                if stop.is_set() and not stop_requested:
                    stop_requested = True
                    stop_deadline = now + self.terminate_grace_seconds
                    _signal_process(process, signal.SIGTERM)
                if (
                    stop_deadline is not None
                    and now >= stop_deadline
                    and process.poll() is None
                ):
                    _signal_process(process, signal.SIGKILL)

                readable, _, _ = select.select(
                    [process.stdout], [], [], self.poll_seconds
                )
                if readable:
                    chunk = _read_available(descriptor)
                    if chunk:
                        captured.extend(chunk)
                        if len(captured) > self.max_capture_bytes:
                            _terminate_process(
                                process, self.terminate_grace_seconds
                            )
                            raise RuntimeError(
                                "PipeWire capture byte limit exceeded"
                            )
                    elif chunk == b"":
                        break

                if process.poll() is not None:
                    captured.extend(_drain_available(descriptor))
                    if len(captured) > self.max_capture_bytes:
                        raise RuntimeError("PipeWire capture byte limit exceeded")
                    break

            try:
                return_code = process.wait(
                    timeout=self.terminate_grace_seconds
                )
            except subprocess.TimeoutExpired:
                _terminate_process(process, self.terminate_grace_seconds)
                raise RuntimeError("PipeWire capture process failed") from None
            if return_code != 0 and not stop_requested:
                raise RuntimeError("PipeWire capture process failed")
            if not captured:
                raise RuntimeError("PipeWire capture produced no audio")
            frame_bytes = self.channels * self.sample_format.bytes_per_sample
            if len(captured) % frame_bytes:
                raise RuntimeError("PipeWire capture produced invalid PCM frames")
            return CapturedAudio(
                data=bytes(captured),
                sample_rate_hz=self.sample_rate_hz,
                channels=self.channels,
                sample_format=self.sample_format,
            )
        finally:
            process.stdout.close()
            if process.poll() is None:
                _terminate_process(process, self.terminate_grace_seconds)

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
