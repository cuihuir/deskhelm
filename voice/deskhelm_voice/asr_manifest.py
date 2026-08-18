from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import wave

from .benchmark import (
    BenchmarkAudioSample,
    BenchmarkCorpus,
    BenchmarkLanguage,
    BenchmarkUtterance,
    MAX_CORPUS_BYTES,
)
from .models import CapturedAudio, PcmSampleFormat


ASR_MANIFEST_VERSION = 1
MAX_ASR_SOURCE_BYTES = 16 << 20
MAX_ASR_SOURCES = 128
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class AsrAudioSource:
    utterance_id: str
    language: BenchmarkLanguage
    text: str
    tags: tuple[str, ...]
    keywords: tuple[str, ...]
    file_name: str
    url: str
    revision: str
    sha256: str
    license: str
    speaker: str

    def __post_init__(self) -> None:
        BenchmarkUtterance(
            self.utterance_id,
            self.language,
            self.text,
            self.tags,
            self.keywords,
        )
        for value, name in (
            (self.file_name, "file_name"),
            (self.url, "url"),
            (self.revision, "revision"),
            (self.license, "license"),
            (self.speaker, "speaker"),
        ):
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError(f"ASR source {name} is invalid")
        if Path(self.file_name).name != self.file_name:
            raise ValueError("ASR source file_name must be a base name")
        if not self.url.startswith("https://"):
            raise ValueError("ASR source URL must use HTTPS")
        if not _REVISION.fullmatch(self.revision):
            raise ValueError("ASR source revision is invalid")
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("ASR source sha256 is invalid")

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> AsrAudioSource:
        try:
            raw_tags = data.get("tags", [])
            raw_keywords = data.get("keywords", [])
            if not isinstance(raw_tags, list) or not isinstance(
                raw_keywords, list
            ):
                raise ValueError("ASR tags and keywords must be lists")
            return cls(
                utterance_id=data["utterance_id"],
                language=BenchmarkLanguage(data["language"]),
                text=data["text"],
                tags=tuple(raw_tags),
                keywords=tuple(raw_keywords),
                file_name=data["file_name"],
                url=data["url"],
                revision=data["revision"],
                sha256=data["sha256"],
                license=data["license"],
                speaker=data["speaker"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid ASR audio source") from error

    def utterance(self) -> BenchmarkUtterance:
        return BenchmarkUtterance(
            self.utterance_id,
            self.language,
            self.text,
            self.tags,
            self.keywords,
        )


@dataclass(frozen=True, slots=True)
class AsrRunManifest:
    name: str
    sample_rate_hz: int
    channels: int
    sample_format: str
    sources: tuple[AsrAudioSource, ...]
    schema_version: int = ASR_MANIFEST_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ASR_MANIFEST_VERSION:
            raise ValueError("unsupported ASR manifest version")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ASR manifest name is invalid")
        if (
            self.sample_rate_hz != 16_000
            or self.channels != 1
            or self.sample_format != "s16le"
        ):
            raise ValueError("ASR manifest requires 16 kHz mono S16LE PCM")
        if not self.sources or len(self.sources) > MAX_ASR_SOURCES:
            raise ValueError("ASR manifest sources are invalid")
        identifiers = [source.utterance_id for source in self.sources]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("ASR utterance IDs must be unique")

    @classmethod
    def load(cls, path: Path) -> AsrRunManifest:
        try:
            if path.stat().st_size > MAX_CORPUS_BYTES:
                raise ValueError("ASR manifest exceeds size limit")
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("unable to read ASR manifest") from error
        if not isinstance(data, Mapping):
            raise ValueError("ASR manifest must be a JSON object")
        try:
            raw_sources = data["sources"]
            if not isinstance(raw_sources, list) or not all(
                isinstance(item, Mapping) for item in raw_sources
            ):
                raise ValueError("ASR sources must be objects")
            return cls(
                schema_version=data["schema_version"],
                name=data["name"],
                sample_rate_hz=data["audio_format"]["sample_rate_hz"],
                channels=data["audio_format"]["channels"],
                sample_format=data["audio_format"]["sample_format"],
                sources=tuple(
                    AsrAudioSource.from_dict(item) for item in raw_sources
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid ASR manifest") from error

    def corpus(self) -> BenchmarkCorpus:
        return BenchmarkCorpus(
            self.name,
            tuple(source.utterance() for source in self.sources),
        )


def load_prepared_asr_set(
    manifest_path: Path,
    prepared_directory: Path,
) -> tuple[BenchmarkCorpus, tuple[BenchmarkAudioSample, ...]]:
    manifest = AsrRunManifest.load(manifest_path)
    index_path = prepared_directory / "index.json"
    try:
        if index_path.stat().st_size > MAX_CORPUS_BYTES:
            raise ValueError("prepared ASR index exceeds size limit")
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("unable to read prepared ASR index") from error
    if (
        not isinstance(index, Mapping)
        or index.get("schema_version") != ASR_MANIFEST_VERSION
        or index.get("manifest_name") != manifest.name
    ):
        raise ValueError("prepared ASR index does not match manifest")
    entries = index.get("samples")
    if not isinstance(entries, list) or not all(
        isinstance(entry, Mapping) for entry in entries
    ):
        raise ValueError("prepared ASR sample index is invalid")
    identifiers = [entry.get("utterance_id") for entry in entries]
    if not all(isinstance(identifier, str) for identifier in identifiers):
        raise ValueError("prepared ASR sample ID is invalid")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("prepared ASR sample IDs must be unique")
    if set(identifiers) != {
        source.utterance_id for source in manifest.sources
    }:
        raise ValueError("prepared ASR samples do not match manifest")
    by_id = dict(zip(identifiers, entries, strict=True))
    samples = []
    for source in manifest.sources:
        entry = by_id.get(source.utterance_id)
        if not isinstance(entry, Mapping):
            raise ValueError("prepared ASR sample is missing")
        file_name = entry.get("file_name")
        if not isinstance(file_name, str) or Path(file_name).name != file_name:
            raise ValueError("prepared ASR file name is invalid")
        pcm = _read_prepared_wav(
            prepared_directory / file_name,
            manifest,
            entry.get("sha256"),
        )
        duration_ms = len(pcm) * 1000 / (
            manifest.sample_rate_hz * manifest.channels * 2
        )
        recorded_duration = entry.get("audio_duration_ms")
        if (
            not isinstance(recorded_duration, (int, float))
            or isinstance(recorded_duration, bool)
            or abs(duration_ms - recorded_duration) > 0.001
        ):
            raise ValueError("prepared ASR duration is invalid")
        samples.append(
            BenchmarkAudioSample(
                source.utterance_id,
                CapturedAudio(
                    pcm,
                    manifest.sample_rate_hz,
                    manifest.channels,
                    PcmSampleFormat.S16LE,
                ),
                duration_ms,
            )
        )
    return manifest.corpus(), tuple(samples)


def _read_prepared_wav(
    path: Path,
    manifest: AsrRunManifest,
    expected_sha256: object,
) -> bytes:
    try:
        if path.stat().st_size > MAX_ASR_SOURCE_BYTES:
            raise ValueError("prepared ASR WAV exceeds size limit")
        payload = path.read_bytes()
        if (
            not isinstance(expected_sha256, str)
            or hashlib.sha256(payload).hexdigest() != expected_sha256
        ):
            raise ValueError("prepared ASR WAV checksum is invalid")
        with wave.open(str(path), "rb") as stream:
            if (
                stream.getcomptype() != "NONE"
                or stream.getsampwidth() != 2
                or stream.getframerate() != manifest.sample_rate_hz
                or stream.getnchannels() != manifest.channels
            ):
                raise ValueError("prepared ASR WAV format is invalid")
            pcm = stream.readframes(stream.getnframes())
    except (OSError, EOFError, wave.Error) as error:
        raise ValueError("unable to read prepared ASR WAV") from error
    if not pcm or len(pcm) > MAX_ASR_SOURCE_BYTES or len(pcm) % 2:
        raise ValueError("prepared ASR PCM is invalid")
    return pcm
