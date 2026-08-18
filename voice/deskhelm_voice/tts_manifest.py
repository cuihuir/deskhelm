from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from .benchmark import MAX_CORPUS_BYTES


TTS_MANIFEST_VERSION = 1
MAX_TTS_ARTIFACT_BYTES = 1 << 30
MAX_TTS_CANDIDATES = 16
MAX_TTS_ARTIFACTS = 16
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")


@dataclass(frozen=True, slots=True)
class TtsArtifact:
    role: str
    file_name: str
    url: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        for value, name in (
            (self.role, "role"),
            (self.file_name, "file_name"),
            (self.url, "url"),
        ):
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError(f"TTS artifact {name} is invalid")
        if Path(self.file_name).name != self.file_name:
            raise ValueError("TTS artifact file_name must be a base name")
        if not self.url.startswith("https://"):
            raise ValueError("TTS artifact URL must use HTTPS")
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("TTS artifact sha256 is invalid")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or not 1 <= self.size_bytes <= MAX_TTS_ARTIFACT_BYTES
        ):
            raise ValueError("TTS artifact size is invalid")

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TtsArtifact:
        try:
            return cls(
                role=data["role"],
                file_name=data["file_name"],
                url=data["url"],
                sha256=data["sha256"],
                size_bytes=data["size_bytes"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid TTS artifact") from error


@dataclass(frozen=True, slots=True)
class TtsCandidate:
    candidate_id: str
    provider_name: str
    provider_version: str
    provider_revision: str
    provider_license: str
    model_name: str
    model_version: str
    model_revision: str
    model_license: str
    artifacts: tuple[TtsArtifact, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.candidate_id, "candidate_id"),
            (self.provider_name, "provider_name"),
            (self.provider_version, "provider_version"),
            (self.provider_license, "provider_license"),
            (self.model_name, "model_name"),
            (self.model_version, "model_version"),
            (self.model_license, "model_license"),
        ):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"TTS candidate {name} is invalid")
        if not _IDENTIFIER.fullmatch(self.candidate_id):
            raise ValueError("TTS candidate ID is invalid")
        if not _REVISION.fullmatch(self.provider_revision):
            raise ValueError("TTS provider revision is invalid")
        if not _REVISION.fullmatch(self.model_revision):
            raise ValueError("TTS model revision is invalid")
        if not self.artifacts or len(self.artifacts) > MAX_TTS_ARTIFACTS:
            raise ValueError("TTS candidate artifacts are invalid")
        roles = [artifact.role for artifact in self.artifacts]
        files = [artifact.file_name for artifact in self.artifacts]
        if len(roles) != len(set(roles)) or len(files) != len(set(files)):
            raise ValueError("TTS candidate artifacts must be unique")

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TtsCandidate:
        try:
            raw_artifacts = data["artifacts"]
            if not isinstance(raw_artifacts, list) or not all(
                isinstance(item, Mapping) for item in raw_artifacts
            ):
                raise ValueError("TTS artifacts must be objects")
            return cls(
                candidate_id=data["candidate_id"],
                provider_name=data["provider_name"],
                provider_version=data["provider_version"],
                provider_revision=data["provider_revision"],
                provider_license=data["provider_license"],
                model_name=data["model_name"],
                model_version=data["model_version"],
                model_revision=data["model_revision"],
                model_license=data["model_license"],
                artifacts=tuple(
                    TtsArtifact.from_dict(item) for item in raw_artifacts
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid TTS candidate") from error

    def artifact_path(self, role: str, prepared_directory: Path) -> Path:
        matches = [item for item in self.artifacts if item.role == role]
        if len(matches) != 1:
            raise ValueError(f"TTS artifact role is unavailable: {role}")
        path = prepared_directory / self.candidate_id / matches[0].file_name
        _verify_artifact(path, matches[0])
        return path


@dataclass(frozen=True, slots=True)
class TtsCandidateManifest:
    name: str
    candidates: tuple[TtsCandidate, ...]
    schema_version: int = TTS_MANIFEST_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != TTS_MANIFEST_VERSION
        ):
            raise ValueError("unsupported TTS manifest version")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("TTS manifest name is invalid")
        if not self.candidates or len(self.candidates) > MAX_TTS_CANDIDATES:
            raise ValueError("TTS manifest candidates are invalid")
        identifiers = [item.candidate_id for item in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("TTS candidate IDs must be unique")

    @classmethod
    def load(cls, path: Path) -> TtsCandidateManifest:
        try:
            if path.stat().st_size > MAX_CORPUS_BYTES:
                raise ValueError("TTS manifest exceeds size limit")
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("unable to read TTS manifest") from error
        if not isinstance(data, Mapping):
            raise ValueError("TTS manifest must be a JSON object")
        try:
            raw_candidates = data["candidates"]
            if not isinstance(raw_candidates, list) or not all(
                isinstance(item, Mapping) for item in raw_candidates
            ):
                raise ValueError("TTS candidates must be objects")
            return cls(
                schema_version=data["schema_version"],
                name=data["name"],
                candidates=tuple(
                    TtsCandidate.from_dict(item) for item in raw_candidates
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid TTS manifest") from error

    def candidate(self, candidate_id: str) -> TtsCandidate:
        matches = [
            item for item in self.candidates if item.candidate_id == candidate_id
        ]
        if len(matches) != 1:
            raise ValueError("unknown TTS candidate")
        return matches[0]


def _verify_artifact(path: Path, artifact: TtsArtifact) -> None:
    try:
        if path.stat().st_size != artifact.size_bytes:
            raise ValueError("TTS artifact size is invalid")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
    except OSError as error:
        raise ValueError("unable to read TTS artifact") from error
    if digest.hexdigest() != artifact.sha256:
        raise ValueError("TTS artifact checksum is invalid")
