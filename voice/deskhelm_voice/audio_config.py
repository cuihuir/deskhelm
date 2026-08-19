from __future__ import annotations

from array import array
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import json
import math
import os
import re
import select
import signal
import subprocess
import sys
from threading import Event, Timer
import time

from .models import CapturedAudio, PcmSampleFormat, SynthesizedAudio
from .pipewire import PipeWireCaptureProvider, PipeWirePlaybackProvider
from .providers import CaptureProvider, PlaybackProvider


MAX_DISCOVERY_BYTES = 1 << 20
MAX_AUDIO_NODES = 1024
MAX_NODE_TEXT_CHARS = 512
DEFAULT_COMMAND_TIMEOUT_SECONDS = 3.0
DEFAULT_TEST_INPUT_SECONDS = 2.0
DEFAULT_TEST_OUTPUT_SECONDS = 0.25
DEFAULT_TEST_TONE_HZ = 660.0
DEFAULT_TEST_TONE_LEVEL = 0.08
_NODE_NAME = re.compile(r'^\s*\*?\s*node\.name\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


class AudioProviderKind(StrEnum):
    PIPEWIRE = "pipewire"


class AudioNodeKind(StrEnum):
    SOURCE = "source"
    SINK = "sink"


@dataclass(frozen=True, slots=True)
class AudioNode:
    kind: AudioNodeKind
    name: str
    description: str
    media_class: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AudioNodeKind):
            raise ValueError("audio node kind is invalid")
        for value, name in (
            (self.name, "name"),
            (self.description, "description"),
            (self.media_class, "media_class"),
        ):
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > MAX_NODE_TEXT_CHARS
                or _has_control_characters(value)
            ):
                raise ValueError(f"audio node {name} is invalid")


@dataclass(frozen=True, slots=True)
class PipeWireAudioInventory:
    sources: tuple[AudioNode, ...]
    sinks: tuple[AudioNode, ...]
    default_source_name: str
    default_sink_name: str

    def __post_init__(self) -> None:
        if not self.sources or not self.sinks:
            raise ValueError("PipeWire audio sources and sinks are required")
        if any(node.kind is not AudioNodeKind.SOURCE for node in self.sources):
            raise ValueError("PipeWire source inventory is invalid")
        if any(node.kind is not AudioNodeKind.SINK for node in self.sinks):
            raise ValueError("PipeWire sink inventory is invalid")
        if len(self.sources) + len(self.sinks) > MAX_AUDIO_NODES:
            raise ValueError("PipeWire audio node limit exceeded")
        names = [node.name for node in (*self.sources, *self.sinks)]
        if len(names) != len(set(names)):
            raise ValueError("PipeWire audio node names must be unique")
        if self.default_source_name not in {node.name for node in self.sources}:
            raise ValueError("PipeWire default source is unavailable")
        if self.default_sink_name not in {node.name for node in self.sinks}:
            raise ValueError("PipeWire default sink is unavailable")

    def source(self, name: str) -> AudioNode:
        return _resolve_node(self.sources, name, "source")

    def sink(self, name: str) -> AudioNode:
        return _resolve_node(self.sinks, name, "sink")


@dataclass(frozen=True, slots=True)
class ResolvedAudioSelection:
    source: AudioNode
    sink: AudioNode
    source_uses_default: bool
    sink_uses_default: bool

    def __post_init__(self) -> None:
        if self.source.kind is not AudioNodeKind.SOURCE:
            raise ValueError("resolved audio source is invalid")
        if self.sink.kind is not AudioNodeKind.SINK:
            raise ValueError("resolved audio sink is invalid")
        if not isinstance(self.source_uses_default, bool):
            raise ValueError("resolved source selection is invalid")
        if not isinstance(self.sink_uses_default, bool):
            raise ValueError("resolved sink selection is invalid")


@dataclass(frozen=True, slots=True)
class LocalAudioConfig:
    capture_provider: AudioProviderKind = AudioProviderKind.PIPEWIRE
    playback_provider: AudioProviderKind = AudioProviderKind.PIPEWIRE
    source_name: str | None = None
    sink_name: str | None = None
    sample_rate_hz: int = 16_000
    channels: int = 1
    latency: str = "20ms"

    def __post_init__(self) -> None:
        if not isinstance(self.capture_provider, AudioProviderKind):
            raise ValueError("capture provider is invalid")
        if not isinstance(self.playback_provider, AudioProviderKind):
            raise ValueError("playback provider is invalid")
        _validate_optional_target(self.source_name, "source")
        _validate_optional_target(self.sink_name, "sink")
        _validate_integer_range(
            self.sample_rate_hz,
            8_000,
            192_000,
            "sample_rate_hz",
        )
        _validate_integer_range(self.channels, 1, 32, "channels")
        if (
            not isinstance(self.latency, str)
            or not self.latency.strip()
            or len(self.latency) > 64
        ):
            raise ValueError("audio latency is invalid")

    def resolve(
        self,
        inventory: PipeWireAudioInventory,
    ) -> ResolvedAudioSelection:
        if not isinstance(inventory, PipeWireAudioInventory):
            raise ValueError("audio inventory is invalid")
        source_name = self.source_name or inventory.default_source_name
        sink_name = self.sink_name or inventory.default_sink_name
        return ResolvedAudioSelection(
            source=inventory.source(source_name),
            sink=inventory.sink(sink_name),
            source_uses_default=self.source_name is None,
            sink_uses_default=self.sink_name is None,
        )

    def create_capture_provider(
        self,
        *,
        max_capture_seconds: float = 30.0,
        max_capture_bytes: int = 1 << 20,
    ) -> CaptureProvider:
        if self.capture_provider is not AudioProviderKind.PIPEWIRE:
            raise ValueError("unsupported capture provider")
        return PipeWireCaptureProvider(
            source_name=self.source_name,
            sample_rate_hz=self.sample_rate_hz,
            channels=self.channels,
            latency=self.latency,
            max_capture_seconds=max_capture_seconds,
            max_capture_bytes=max_capture_bytes,
        )

    def create_playback_provider(self) -> PlaybackProvider:
        if self.playback_provider is not AudioProviderKind.PIPEWIRE:
            raise ValueError("unsupported playback provider")
        return PipeWirePlaybackProvider(
            sink_name=self.sink_name,
            latency=self.latency,
        )


@dataclass(frozen=True, slots=True)
class AudioSignalReport:
    duration_ms: float
    sample_rate_hz: int
    channels: int
    sample_format: str
    bytes_captured: int
    peak_fraction: float
    rms_fraction: float


CommandRunner = Callable[[tuple[str, ...]], str]


def discover_pipewire_audio(
    *,
    pw_dump_executable: str = "pw-dump",
    wpctl_executable: str = "wpctl",
    command_runner: CommandRunner | None = None,
) -> PipeWireAudioInventory:
    runner = command_runner or _run_bounded_command
    dump_text = _validate_discovery_text(
        runner((pw_dump_executable, "--no-colors"))
    )
    source_text = _validate_discovery_text(
        runner((wpctl_executable, "inspect", "@DEFAULT_AUDIO_SOURCE@"))
    )
    sink_text = _validate_discovery_text(
        runner((wpctl_executable, "inspect", "@DEFAULT_AUDIO_SINK@"))
    )
    try:
        objects = json.loads(dump_text)
    except json.JSONDecodeError as error:
        raise RuntimeError("PipeWire discovery returned invalid data") from error
    if not isinstance(objects, list) or len(objects) > MAX_AUDIO_NODES * 8:
        raise RuntimeError("PipeWire discovery returned invalid data")
    sources = []
    sinks = []
    for item in objects:
        node = _parse_node(item)
        if node is None:
            continue
        if node.kind is AudioNodeKind.SOURCE:
            sources.append(node)
        else:
            sinks.append(node)
        if len(sources) + len(sinks) > MAX_AUDIO_NODES:
            raise RuntimeError("PipeWire audio node limit exceeded")
    return PipeWireAudioInventory(
        sources=tuple(sorted(sources, key=lambda node: node.name)),
        sinks=tuple(sorted(sinks, key=lambda node: node.name)),
        default_source_name=_parse_default_name(source_text, "source"),
        default_sink_name=_parse_default_name(sink_text, "sink"),
    )


def test_audio_input(
    provider: CaptureProvider,
    *,
    seconds: float = DEFAULT_TEST_INPUT_SECONDS,
) -> AudioSignalReport:
    _validate_test_seconds(seconds, 10.0)
    stop = Event()
    cancel = Event()
    timer = Timer(seconds, stop.set)
    timer.daemon = True
    timer.start()
    try:
        audio = provider.capture(stop, cancel)
    finally:
        timer.cancel()
    return measure_audio_signal(audio)


def measure_audio_signal(audio: CapturedAudio) -> AudioSignalReport:
    if not isinstance(audio, CapturedAudio):
        raise ValueError("captured audio is invalid")
    if audio.sample_format is not PcmSampleFormat.S16LE:
        raise ValueError("audio signal measurement requires S16LE PCM")
    samples = array("h")
    samples.frombytes(audio.data)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(sample) for sample in samples) / 32768
    rms = math.sqrt(
        sum(sample * sample for sample in samples) / len(samples)
    ) / 32768
    return AudioSignalReport(
        duration_ms=audio.duration_seconds * 1000,
        sample_rate_hz=audio.sample_rate_hz,
        channels=audio.channels,
        sample_format=audio.sample_format.value,
        bytes_captured=len(audio.data),
        peak_fraction=peak,
        rms_fraction=rms,
    )


def create_test_tone(
    *,
    seconds: float = DEFAULT_TEST_OUTPUT_SECONDS,
    frequency_hz: float = DEFAULT_TEST_TONE_HZ,
    level: float = DEFAULT_TEST_TONE_LEVEL,
    sample_rate_hz: int = 24_000,
) -> SynthesizedAudio:
    _validate_test_seconds(seconds, 2.0)
    if (
        not isinstance(frequency_hz, (int, float))
        or isinstance(frequency_hz, bool)
        or not math.isfinite(frequency_hz)
        or not 20 <= frequency_hz <= 10_000
    ):
        raise ValueError("test tone frequency is invalid")
    if (
        not isinstance(level, (int, float))
        or isinstance(level, bool)
        or not math.isfinite(level)
        or not 0 < level <= 0.25
    ):
        raise ValueError("test tone level is invalid")
    _validate_integer_range(sample_rate_hz, 8_000, 192_000, "sample_rate_hz")
    frame_count = max(1, round(seconds * sample_rate_hz))
    amplitude = round(level * 32767)
    samples = array(
        "h",
        (
            round(
                amplitude
                * math.sin(2 * math.pi * frequency_hz * frame / sample_rate_hz)
            )
            for frame in range(frame_count)
        ),
    )
    if sys.byteorder != "little":
        samples.byteswap()
    return SynthesizedAudio(samples.tobytes(), sample_rate_hz)


def _parse_node(value: object) -> AudioNode | None:
    if not isinstance(value, dict):
        return None
    info = value.get("info")
    if not isinstance(info, dict):
        return None
    props = info.get("props")
    if not isinstance(props, dict):
        return None
    media_class = props.get("media.class")
    name = props.get("node.name")
    if not isinstance(media_class, str) or not isinstance(name, str):
        return None
    if media_class.startswith("Audio/Source"):
        kind = AudioNodeKind.SOURCE
    elif media_class.startswith("Audio/Sink"):
        kind = AudioNodeKind.SINK
    else:
        return None
    description = props.get("node.description")
    if not isinstance(description, str) or not description.strip():
        description = name
    try:
        return AudioNode(kind, name, description, media_class)
    except ValueError as error:
        raise RuntimeError("PipeWire discovery returned invalid node data") from error


def _parse_default_name(value: str, kind: str) -> str:
    match = _NODE_NAME.search(value)
    if match is None or len(match.group(1)) > MAX_NODE_TEXT_CHARS:
        raise RuntimeError(f"PipeWire default {kind} is unavailable")
    return match.group(1)


def _resolve_node(
    nodes: tuple[AudioNode, ...],
    name: str,
    kind: str,
) -> AudioNode:
    matches = [node for node in nodes if node.name == name]
    if len(matches) != 1:
        raise ValueError(f"configured PipeWire {kind} is unavailable")
    return matches[0]


def _run_bounded_command(command: tuple[str, ...]) -> str:
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ValueError("PipeWire discovery command is invalid")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            bufsize=0,
        )
    except OSError as error:
        raise RuntimeError("PipeWire discovery could not start") from error
    assert process.stdout is not None
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    deadline = time.monotonic() + DEFAULT_COMMAND_TIMEOUT_SECONDS
    output = bytearray()
    try:
        while True:
            if time.monotonic() >= deadline:
                _terminate(process)
                raise RuntimeError("PipeWire discovery timed out")
            readable, _, _ = select.select([process.stdout], [], [], 0.05)
            if readable:
                try:
                    chunk = os.read(descriptor, 65_536)
                except BlockingIOError:
                    chunk = None
                if chunk:
                    output.extend(chunk)
                    if len(output) > MAX_DISCOVERY_BYTES:
                        _terminate(process)
                        raise RuntimeError("PipeWire discovery output is too large")
                elif chunk == b"":
                    break
            if process.poll() is not None and not readable:
                break
        return_code = process.wait(timeout=0.5)
        if return_code != 0:
            raise RuntimeError("PipeWire discovery failed")
        try:
            return output.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RuntimeError("PipeWire discovery returned invalid text") from error
    finally:
        process.stdout.close()
        if process.poll() is None:
            _terminate(process)


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=0.5)
        return
    except (ProcessLookupError, PermissionError):
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=0.5)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        pass


def _validate_optional_target(value: str | None, kind: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"audio {kind} name is invalid")
    if len(value) > MAX_NODE_TEXT_CHARS:
        raise ValueError(f"audio {kind} name is invalid")
    if _has_control_characters(value):
        raise ValueError(f"audio {kind} name is invalid")
    if value.strip().isdigit():
        raise ValueError(f"audio {kind} must use a stable node name")


def _validate_integer_range(
    value: object,
    minimum: int,
    maximum: int,
    name: str,
) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} is outside the supported range")


def _validate_discovery_text(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError("PipeWire discovery returned invalid text")
    if len(value.encode("utf-8")) > MAX_DISCOVERY_BYTES:
        raise RuntimeError("PipeWire discovery output is too large")
    return value


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _validate_test_seconds(value: float, maximum: float) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0.1 <= value <= maximum
    ):
        raise ValueError("audio test duration is invalid")
