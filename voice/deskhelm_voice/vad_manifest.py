from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re


VAD_MANIFEST_VERSION = 1
MAX_MANIFEST_BYTES = 1 << 20
MAX_SOURCES = 64
MAX_SCENARIOS = 128
MAX_PARTS = 256
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class VadAudioSource:
    source_id: str
    file_name: str
    url: str
    revision: str
    sha256: str
    license: str
    speaker: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.source_id, "source_id"),
            (self.file_name, "file_name"),
            (self.url, "url"),
            (self.revision, "revision"),
            (self.license, "license"),
            (self.speaker, "speaker"),
        ):
            _validate_text(value, name)
        if Path(self.file_name).name != self.file_name:
            raise ValueError("source file_name must be a base name")
        if not self.url.startswith("https://"):
            raise ValueError("source URL must use HTTPS")
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise ValueError("source sha256 is invalid")


@dataclass(frozen=True, slots=True)
class VadScenarioPart:
    source_id: str | None = None
    silence_ms: int | None = None

    def __post_init__(self) -> None:
        has_source = self.source_id is not None
        has_silence = self.silence_ms is not None
        if has_source == has_silence:
            raise ValueError("scenario part must select source or silence")
        if has_source:
            _validate_text(self.source_id, "part source_id")
        if has_silence and (
            not isinstance(self.silence_ms, int)
            or isinstance(self.silence_ms, bool)
            or not 20 <= self.silence_ms <= 60_000
        ):
            raise ValueError("scenario silence_ms is invalid")


@dataclass(frozen=True, slots=True)
class VadScenario:
    scenario_id: str
    parts: tuple[VadScenarioPart, ...]

    def __post_init__(self) -> None:
        _validate_text(self.scenario_id, "scenario_id")
        if not self.parts or len(self.parts) > MAX_PARTS:
            raise ValueError("scenario parts are invalid")
        if not any(part.source_id is not None for part in self.parts):
            raise ValueError("scenario must contain speech")


@dataclass(frozen=True, slots=True)
class VadRunManifest:
    name: str
    source_dataset: str
    dataset_url: str
    dataset_revision: str
    dataset_license: str
    sample_rate_hz: int
    channels: int
    sample_format: str
    chunk_ms: int
    sources: tuple[VadAudioSource, ...]
    scenarios: tuple[VadScenario, ...]
    schema_version: int = VAD_MANIFEST_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != VAD_MANIFEST_VERSION:
            raise ValueError("unsupported VAD manifest version")
        for value, name in (
            (self.name, "name"),
            (self.source_dataset, "source_dataset"),
            (self.dataset_url, "dataset_url"),
            (self.dataset_revision, "dataset_revision"),
            (self.dataset_license, "dataset_license"),
        ):
            _validate_text(value, name)
        if not self.dataset_url.startswith("https://"):
            raise ValueError("dataset URL must use HTTPS")
        if self.sample_rate_hz not in (8_000, 16_000, 32_000, 48_000):
            raise ValueError("unsupported VAD sample rate")
        if self.channels != 1 or self.sample_format != "s16le":
            raise ValueError("VAD manifest requires mono S16LE PCM")
        if self.chunk_ms not in (10, 20, 30):
            raise ValueError("VAD chunk_ms must be 10, 20, or 30")
        if not self.sources or len(self.sources) > MAX_SOURCES:
            raise ValueError("VAD manifest sources are invalid")
        if not self.scenarios or len(self.scenarios) > MAX_SCENARIOS:
            raise ValueError("VAD manifest scenarios are invalid")
        source_ids = {source.source_id for source in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("VAD source IDs must be unique")
        scenario_ids = {scenario.scenario_id for scenario in self.scenarios}
        if len(scenario_ids) != len(self.scenarios):
            raise ValueError("VAD scenario IDs must be unique")
        if any(
            part.source_id is not None and part.source_id not in source_ids
            for scenario in self.scenarios
            for part in scenario.parts
        ):
            raise ValueError("VAD scenario references unknown source")

    @classmethod
    def load(cls, path: Path) -> VadRunManifest:
        try:
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                raise ValueError("VAD manifest exceeds size limit")
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("unable to read VAD manifest") from error
        if not isinstance(data, Mapping):
            raise ValueError("VAD manifest must be a JSON object")
        try:
            raw_sources = data["sources"]
            raw_scenarios = data["scenarios"]
            if not isinstance(raw_sources, list) or not isinstance(
                raw_scenarios, list
            ):
                raise ValueError("VAD manifest collections must be lists")
            return cls(
                schema_version=data["schema_version"],
                name=data["name"],
                source_dataset=data["source_dataset"],
                dataset_url=data["dataset_url"],
                dataset_revision=data["dataset_revision"],
                dataset_license=data["dataset_license"],
                sample_rate_hz=data["audio_format"]["sample_rate_hz"],
                channels=data["audio_format"]["channels"],
                sample_format=data["audio_format"]["sample_format"],
                chunk_ms=data["chunk_ms"],
                sources=tuple(VadAudioSource(**item) for item in raw_sources),
                scenarios=tuple(
                    VadScenario(
                        scenario_id=item["scenario_id"],
                        parts=tuple(
                            VadScenarioPart(**part) for part in item["parts"]
                        ),
                    )
                    for item in raw_scenarios
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid VAD manifest") from error


def _validate_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"{name} is invalid")
