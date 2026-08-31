from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re


VAD_MANIFEST_VERSION = 2
SUPPORTED_VAD_MANIFEST_VERSIONS = (1, 2)
MAX_MANIFEST_BYTES = 1 << 20
MAX_SOURCES = 64
MAX_SCENARIOS = 128
MAX_PARTS = 256
MAX_REFERENCE_SEGMENTS = 256
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class VadReferenceSegmentMs:
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.start_ms, int)
            or isinstance(self.start_ms, bool)
            or not isinstance(self.end_ms, int)
            or isinstance(self.end_ms, bool)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
        ):
            raise ValueError("source speech segment is invalid")


@dataclass(frozen=True, slots=True)
class VadAudioSource:
    source_id: str
    file_name: str
    url: str
    revision: str
    sha256: str
    license: str
    speaker: str
    source_type: str = "speech"
    clip_start_ms: int = 0
    clip_duration_ms: int | None = None
    speech_segments_ms: tuple[VadReferenceSegmentMs, ...] = ()
    annotation_url: str | None = None
    annotation_revision: str | None = None
    annotation_sha256: str | None = None

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
        if self.source_type not in ("speech", "noise"):
            raise ValueError("source type is invalid")
        if (
            not isinstance(self.clip_start_ms, int)
            or isinstance(self.clip_start_ms, bool)
            or not 0 <= self.clip_start_ms <= 3_600_000
        ):
            raise ValueError("source clip_start_ms is invalid")
        if self.clip_duration_ms is not None and (
            not isinstance(self.clip_duration_ms, int)
            or isinstance(self.clip_duration_ms, bool)
            or not 20 <= self.clip_duration_ms <= 120_000
        ):
            raise ValueError("source clip_duration_ms is invalid")
        if self.source_type == "noise" and self.speech_segments_ms:
            raise ValueError("noise source cannot contain speech segments")
        if self.speech_segments_ms and self.clip_duration_ms is None:
            raise ValueError("source speech segments require a bounded clip")
        if len(self.speech_segments_ms) > MAX_REFERENCE_SEGMENTS:
            raise ValueError("source has too many speech segments")
        previous_end = 0
        for segment in self.speech_segments_ms:
            if not isinstance(segment, VadReferenceSegmentMs):
                raise ValueError("source speech segment is invalid")
            if segment.start_ms < previous_end:
                raise ValueError("source speech segments overlap")
            if (
                self.clip_duration_ms is not None
                and segment.end_ms > self.clip_duration_ms
            ):
                raise ValueError("source speech segment exceeds clip")
            previous_end = segment.end_ms
        annotation_values = (
            self.annotation_url,
            self.annotation_revision,
            self.annotation_sha256,
        )
        if any(value is not None for value in annotation_values):
            if not all(isinstance(value, str) and value for value in annotation_values):
                raise ValueError("source annotation identity is incomplete")
            assert self.annotation_url is not None
            assert self.annotation_sha256 is not None
            if not self.annotation_url.startswith("https://"):
                raise ValueError("source annotation URL must use HTTPS")
            if not _SHA256.fullmatch(self.annotation_sha256):
                raise ValueError("source annotation sha256 is invalid")


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
    noise_source_id: str | None = None
    snr_db: int | None = None

    def __post_init__(self) -> None:
        _validate_text(self.scenario_id, "scenario_id")
        if not self.parts or len(self.parts) > MAX_PARTS:
            raise ValueError("scenario parts are invalid")
        if not any(part.source_id is not None for part in self.parts):
            raise ValueError("scenario must contain speech")
        has_noise = self.noise_source_id is not None
        has_snr = self.snr_db is not None
        if has_noise != has_snr:
            raise ValueError("scenario noise source and SNR must be combined")
        if has_noise:
            _validate_text(self.noise_source_id, "noise_source_id")
            if (
                not isinstance(self.snr_db, int)
                or isinstance(self.snr_db, bool)
                or not -20 <= self.snr_db <= 40
            ):
                raise ValueError("scenario snr_db is invalid")


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
        if self.schema_version not in SUPPORTED_VAD_MANIFEST_VERSIONS:
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
        sources_by_id = {source.source_id: source for source in self.sources}
        if any(
            part.source_id is not None
            and sources_by_id[part.source_id].source_type != "speech"
            for scenario in self.scenarios
            for part in scenario.parts
        ):
            raise ValueError("VAD scenario speech part references non-speech source")
        for scenario in self.scenarios:
            if scenario.noise_source_id is None:
                continue
            source = sources_by_id.get(scenario.noise_source_id)
            if source is None or source.source_type != "noise":
                raise ValueError("VAD scenario noise source is invalid")

    def source(self, source_id: str) -> VadAudioSource:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise ValueError("VAD source is unavailable")

    def expected_reference_segments(self, scenario: VadScenario) -> int:
        count = 0
        for part in scenario.parts:
            if part.source_id is None:
                continue
            source = self.source(part.source_id)
            count += len(source.speech_segments_ms) or 1
        return count

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
            schema_version = data["schema_version"]
            return cls(
                schema_version=schema_version,
                name=data["name"],
                source_dataset=data["source_dataset"],
                dataset_url=data["dataset_url"],
                dataset_revision=data["dataset_revision"],
                dataset_license=data["dataset_license"],
                sample_rate_hz=data["audio_format"]["sample_rate_hz"],
                channels=data["audio_format"]["channels"],
                sample_format=data["audio_format"]["sample_format"],
                chunk_ms=data["chunk_ms"],
                sources=tuple(
                    _parse_source(item, schema_version) for item in raw_sources
                ),
                scenarios=tuple(
                    VadScenario(
                        scenario_id=item["scenario_id"],
                        parts=tuple(
                            VadScenarioPart(**part) for part in item["parts"]
                        ),
                        noise_source_id=item.get("noise_source_id"),
                        snr_db=item.get("snr_db"),
                    )
                    for item in raw_scenarios
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid VAD manifest") from error


def _validate_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"{name} is invalid")


def _parse_source(item: object, schema_version: object) -> VadAudioSource:
    if not isinstance(item, Mapping):
        raise ValueError("VAD source must be an object")
    raw_segments = item.get("speech_segments_ms", [])
    if not isinstance(raw_segments, list):
        raise ValueError("source speech segments must be a list")
    if schema_version == 1 and any(
        key in item
        for key in (
            "source_type",
            "clip_start_ms",
            "clip_duration_ms",
            "speech_segments_ms",
            "annotation_url",
            "annotation_revision",
            "annotation_sha256",
        )
    ):
        raise ValueError("version 1 VAD source contains version 2 fields")
    return VadAudioSource(
        source_id=item["source_id"],
        file_name=item["file_name"],
        url=item["url"],
        revision=item["revision"],
        sha256=item["sha256"],
        license=item["license"],
        speaker=item["speaker"],
        source_type=item.get("source_type", "speech"),
        clip_start_ms=item.get("clip_start_ms", 0),
        clip_duration_ms=item.get("clip_duration_ms"),
        speech_segments_ms=tuple(
            VadReferenceSegmentMs(
                start_ms=segment["start_ms"],
                end_ms=segment["end_ms"],
            )
            for segment in raw_segments
        ),
        annotation_url=item.get("annotation_url"),
        annotation_revision=item.get("annotation_revision"),
        annotation_sha256=item.get("annotation_sha256"),
    )
