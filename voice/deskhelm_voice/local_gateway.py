from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .audio_config import (
    LocalAudioConfig,
    PipeWireAudioInventory,
    ResolvedAudioSelection,
)
from .gateway import VoiceGateway
from .paraformer import ParaformerStreamingAsrProvider
from .piper_tts import PiperTtsProvider
from .webrtc_vad import WebRtcVadProvider


class LocalAsrProviderKind(StrEnum):
    PARAFORMER = "paraformer"


class LocalTtsProviderKind(StrEnum):
    PIPER = "piper"


class LocalVadProviderKind(StrEnum):
    NONE = "none"
    WEBRTC = "webrtc"


@dataclass(frozen=True, slots=True)
class LocalVoiceComposition:
    gateway: VoiceGateway
    audio_selection: ResolvedAudioSelection


@dataclass(frozen=True, slots=True)
class LocalVoiceConfig:
    audio: LocalAudioConfig
    asr_provider: LocalAsrProviderKind
    asr_model_directory: Path
    tts_provider: LocalTtsProviderKind
    tts_model_path: Path
    tts_config_path: Path
    tts_resource_directory: Path
    cpu_threads: int = 4
    max_capture_seconds: float = 30.0
    max_capture_bytes: int = 1 << 20
    max_speech_items: int = 8
    vad_provider: LocalVadProviderKind = LocalVadProviderKind.NONE

    def __post_init__(self) -> None:
        if not isinstance(self.audio, LocalAudioConfig):
            raise ValueError("local voice audio configuration is invalid")
        if self.audio.sample_rate_hz != 16_000 or self.audio.channels != 1:
            raise ValueError("local Paraformer voice requires 16 kHz mono audio")
        if not isinstance(self.asr_provider, LocalAsrProviderKind):
            raise ValueError("local ASR provider is invalid")
        if not isinstance(self.tts_provider, LocalTtsProviderKind):
            raise ValueError("local TTS provider is invalid")
        if not isinstance(self.vad_provider, LocalVadProviderKind):
            raise ValueError("local VAD provider is invalid")
        for path, name in (
            (self.asr_model_directory, "ASR model directory"),
            (self.tts_model_path, "TTS model path"),
            (self.tts_config_path, "TTS config path"),
            (self.tts_resource_directory, "TTS resource directory"),
        ):
            if not isinstance(path, Path) or not str(path):
                raise ValueError(f"{name} is invalid")
        if (
            not isinstance(self.cpu_threads, int)
            or isinstance(self.cpu_threads, bool)
            or not 1 <= self.cpu_threads <= 32
        ):
            raise ValueError("local voice CPU thread count is invalid")
        if (
            not isinstance(self.max_capture_seconds, (int, float))
            or isinstance(self.max_capture_seconds, bool)
            or not 0 < self.max_capture_seconds <= 120
        ):
            raise ValueError("local voice capture duration limit is invalid")
        if (
            not isinstance(self.max_capture_bytes, int)
            or isinstance(self.max_capture_bytes, bool)
            or not 2 <= self.max_capture_bytes <= 64 << 20
        ):
            raise ValueError("local voice capture byte limit is invalid")
        if (
            not isinstance(self.max_speech_items, int)
            or isinstance(self.max_speech_items, bool)
            or not 1 <= self.max_speech_items <= 64
        ):
            raise ValueError("local voice speech queue limit is invalid")

    def compose(
        self,
        inventory: PipeWireAudioInventory,
    ) -> LocalVoiceComposition:
        selection = self.audio.resolve(inventory)
        self._validate_artifacts()
        gateway = VoiceGateway(
            capture_provider=self.audio.create_capture_provider(
                max_capture_seconds=self.max_capture_seconds,
                max_capture_bytes=self.max_capture_bytes,
            ),
            asr_provider=ParaformerStreamingAsrProvider(
                str(self.asr_model_directory),
                cpu_threads=self.cpu_threads,
                max_audio_seconds=self.max_capture_seconds,
            ),
            tts_provider=PiperTtsProvider(
                str(self.tts_model_path),
                str(self.tts_config_path),
                str(self.tts_resource_directory),
                cpu_threads=self.cpu_threads,
                max_output_bytes=16 << 20,
            ),
            playback_provider=self.audio.create_playback_provider(),
            vad_provider=(
                WebRtcVadProvider()
                if self.vad_provider is LocalVadProviderKind.WEBRTC
                else None
            ),
            max_capture_seconds=self.max_capture_seconds,
            max_capture_bytes=self.max_capture_bytes,
            max_speech_items=self.max_speech_items,
        )
        return LocalVoiceComposition(gateway, selection)

    def _validate_artifacts(self) -> None:
        _require_directory(self.asr_model_directory, "Paraformer model directory")
        for relative in (
            "model.pt",
            "config.yaml",
            "tokens.json",
            "am.mvn",
            "seg_dict",
        ):
            _require_file(
                self.asr_model_directory / relative,
                f"Paraformer {relative}",
            )
        _require_file(self.tts_model_path, "Piper model")
        _require_file(self.tts_config_path, "Piper config")
        _require_directory(self.tts_resource_directory, "Piper resource directory")
        for relative in (
            "g2pW/g2pw.onnx",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.txt",
        ):
            _require_file(
                self.tts_resource_directory / relative,
                f"Piper {relative}",
            )


def _require_file(path: Path, name: str) -> None:
    if not path.is_file():
        raise ValueError(f"{name} is unavailable")


def _require_directory(path: Path, name: str) -> None:
    if not path.is_dir():
        raise ValueError(f"{name} is unavailable")
