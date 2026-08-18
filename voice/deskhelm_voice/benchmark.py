from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import json
import math
from pathlib import Path
import re
from threading import Event
import time
from typing import TextIO
import unicodedata
import uuid

from .models import CapturedAudio
from .providers import AsrProvider, TtsProvider


BENCHMARK_SCHEMA_VERSION = 1
MAX_CORPUS_BYTES = 1 << 20
MAX_NDJSON_RECORD_BYTES = 1 << 20
MAX_OBSERVATION_FILE_BYTES = 64 << 20
MAX_OBSERVATIONS = 10_000
MAX_TEXT_CHARS = 4_096
MAX_KEYWORDS = 32


class BenchmarkLanguage(StrEnum):
    ZH_CN = "zh-CN"
    EN_US = "en-US"
    MIXED = "zh-CN+en-US"


class BenchmarkStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BenchmarkIdentity:
    run_id: str
    provider_name: str
    provider_version: str
    model_name: str
    model_version: str
    provider_license: str
    model_license: str
    system_profile: str
    device: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.run_id, "run_id"),
            (self.provider_name, "provider_name"),
            (self.provider_version, "provider_version"),
            (self.model_name, "model_name"),
            (self.model_version, "model_version"),
            (self.provider_license, "provider_license"),
            (self.model_license, "model_license"),
            (self.system_profile, "system_profile"),
            (self.device, "device"),
        ):
            _validate_text(value, name)

    @classmethod
    def create(
        cls,
        *,
        provider_name: str,
        provider_version: str,
        model_name: str,
        model_version: str,
        provider_license: str,
        model_license: str,
        system_profile: str,
        device: str,
    ) -> BenchmarkIdentity:
        return cls(
            run_id=str(uuid.uuid4()),
            provider_name=provider_name,
            provider_version=provider_version,
            model_name=model_name,
            model_version=model_version,
            provider_license=provider_license,
            model_license=model_license,
            system_profile=system_profile,
            device=device,
        )


@dataclass(frozen=True, slots=True)
class BenchmarkUtterance:
    utterance_id: str
    language: BenchmarkLanguage
    text: str = field(repr=False)
    tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _validate_text(self.utterance_id, "utterance_id")
        if not isinstance(self.language, BenchmarkLanguage):
            raise ValueError("benchmark language is invalid")
        _validate_text(self.text, "utterance text")
        if len(self.text) > MAX_TEXT_CHARS:
            raise ValueError("utterance text is too long")
        _validate_unique_texts(self.tags, "tags")
        _validate_unique_texts(self.keywords, "keywords")
        if len(self.keywords) > MAX_KEYWORDS:
            raise ValueError("utterance has too many keywords")

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> BenchmarkUtterance:
        try:
            language = BenchmarkLanguage(data["language"])
            tags = _text_tuple(data.get("tags", []), "tags")
            keywords = _text_tuple(data.get("keywords", []), "keywords")
            return cls(
                utterance_id=data["utterance_id"],
                language=language,
                text=data["text"],
                tags=tags,
                keywords=keywords,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid benchmark utterance") from error

    def to_dict(self) -> dict[str, object]:
        return {
            "utterance_id": self.utterance_id,
            "language": self.language.value,
            "text": self.text,
            "tags": list(self.tags),
            "keywords": list(self.keywords),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkCorpus:
    name: str
    utterances: tuple[BenchmarkUtterance, ...]
    schema_version: int = BENCHMARK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != BENCHMARK_SCHEMA_VERSION
        ):
            raise ValueError("unsupported benchmark corpus version")
        _validate_text(self.name, "corpus name")
        if not self.utterances:
            raise ValueError("benchmark corpus must not be empty")
        if len(self.utterances) > MAX_OBSERVATIONS:
            raise ValueError("benchmark corpus has too many utterances")
        identifiers = [item.utterance_id for item in self.utterances]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("benchmark utterance IDs must be unique")

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> BenchmarkCorpus:
        try:
            raw_utterances = data["utterances"]
            if not isinstance(raw_utterances, list):
                raise ValueError("utterances must be a list")
            if not all(isinstance(item, Mapping) for item in raw_utterances):
                raise ValueError("utterances must contain objects")
            return cls(
                schema_version=data["schema_version"],
                name=data["name"],
                utterances=tuple(
                    BenchmarkUtterance.from_dict(item)
                    for item in raw_utterances
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid benchmark corpus") from error

    @classmethod
    def load(cls, path: Path) -> BenchmarkCorpus:
        try:
            if path.stat().st_size > MAX_CORPUS_BYTES:
                raise ValueError("benchmark corpus exceeds size limit")
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("unable to read benchmark corpus") from error
        if not isinstance(data, Mapping):
            raise ValueError("benchmark corpus must be a JSON object")
        return cls.from_dict(data)

    def by_id(self) -> dict[str, BenchmarkUtterance]:
        return {item.utterance_id: item for item in self.utterances}

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "utterances": [item.to_dict() for item in self.utterances],
        }


@dataclass(frozen=True, slots=True)
class BenchmarkAudioSample:
    utterance_id: str
    audio: CapturedAudio
    audio_duration_ms: float

    def __post_init__(self) -> None:
        _validate_text(self.utterance_id, "utterance_id")
        if not isinstance(self.audio, CapturedAudio):
            raise ValueError("benchmark audio is invalid")
        _validate_non_negative_number(self.audio_duration_ms, "audio_duration_ms")


@dataclass(frozen=True, slots=True)
class AsrBenchmarkObservation:
    identity: BenchmarkIdentity
    utterance_id: str
    repetition: int
    status: BenchmarkStatus
    audio_duration_ms: float
    final_latency_ms: float
    process_cpu_ms: float
    transcript: str = field(default="", repr=False)
    first_partial_latency_ms: float | None = None
    peak_rss_mib: float | None = None
    peak_vram_mib: float | None = None
    error_code: str = ""
    schema_version: int = BENCHMARK_SCHEMA_VERSION
    record_type: str = "asr_observation"

    def __post_init__(self) -> None:
        _validate_observation_common(
            self.schema_version,
            self.record_type,
            "asr_observation",
            self.identity,
            self.utterance_id,
            self.repetition,
            self.status,
            self.error_code,
        )
        _validate_non_negative_number(self.audio_duration_ms, "audio_duration_ms")
        _validate_non_negative_number(self.final_latency_ms, "final_latency_ms")
        _validate_non_negative_number(self.process_cpu_ms, "process_cpu_ms")
        if self.first_partial_latency_ms is not None:
            _validate_non_negative_number(
                self.first_partial_latency_ms, "first_partial_latency_ms"
            )
        _validate_optional_number(self.peak_rss_mib, "peak_rss_mib")
        _validate_optional_number(self.peak_vram_mib, "peak_vram_mib")
        if self.status is BenchmarkStatus.OK:
            _validate_text(self.transcript, "transcript")
            if len(self.transcript) > MAX_TEXT_CHARS:
                raise ValueError("transcript is too long")
        elif self.transcript:
            raise ValueError("failed ASR observation must not contain transcript")

    def to_dict(self) -> dict[str, object]:
        data = _observation_dict(self)
        data.update(
            {
                "audio_duration_ms": self.audio_duration_ms,
                "final_latency_ms": self.final_latency_ms,
                "process_cpu_ms": self.process_cpu_ms,
                "transcript": self.transcript,
                "first_partial_latency_ms": self.first_partial_latency_ms,
                "peak_rss_mib": self.peak_rss_mib,
                "peak_vram_mib": self.peak_vram_mib,
            }
        )
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> AsrBenchmarkObservation:
        try:
            return cls(
                identity=_identity_from_dict(data),
                utterance_id=data["utterance_id"],
                repetition=data["repetition"],
                status=BenchmarkStatus(data["status"]),
                audio_duration_ms=data["audio_duration_ms"],
                final_latency_ms=data["final_latency_ms"],
                process_cpu_ms=data["process_cpu_ms"],
                transcript=data.get("transcript", ""),
                first_partial_latency_ms=data.get("first_partial_latency_ms"),
                peak_rss_mib=data.get("peak_rss_mib"),
                peak_vram_mib=data.get("peak_vram_mib"),
                error_code=data.get("error_code", ""),
                schema_version=data["schema_version"],
                record_type=data["record_type"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid ASR benchmark observation") from error


@dataclass(frozen=True, slots=True)
class TtsBenchmarkObservation:
    identity: BenchmarkIdentity
    utterance_id: str
    repetition: int
    status: BenchmarkStatus
    synthesis_latency_ms: float
    process_cpu_ms: float
    output_bytes: int
    peak_rss_mib: float | None = None
    peak_vram_mib: float | None = None
    error_code: str = ""
    schema_version: int = BENCHMARK_SCHEMA_VERSION
    record_type: str = "tts_observation"

    def __post_init__(self) -> None:
        _validate_observation_common(
            self.schema_version,
            self.record_type,
            "tts_observation",
            self.identity,
            self.utterance_id,
            self.repetition,
            self.status,
            self.error_code,
        )
        _validate_non_negative_number(
            self.synthesis_latency_ms, "synthesis_latency_ms"
        )
        _validate_non_negative_number(self.process_cpu_ms, "process_cpu_ms")
        _validate_optional_number(self.peak_rss_mib, "peak_rss_mib")
        _validate_optional_number(self.peak_vram_mib, "peak_vram_mib")
        if not isinstance(self.output_bytes, int) or isinstance(
            self.output_bytes, bool
        ) or self.output_bytes < 0:
            raise ValueError("output_bytes must be a non-negative integer")
        if self.status is BenchmarkStatus.OK and self.output_bytes == 0:
            raise ValueError("successful TTS observation requires output bytes")
        if self.status is BenchmarkStatus.FAILED and self.output_bytes != 0:
            raise ValueError("failed TTS observation must not contain output")

    def to_dict(self) -> dict[str, object]:
        data = _observation_dict(self)
        data.update(
            {
                "synthesis_latency_ms": self.synthesis_latency_ms,
                "process_cpu_ms": self.process_cpu_ms,
                "output_bytes": self.output_bytes,
                "peak_rss_mib": self.peak_rss_mib,
                "peak_vram_mib": self.peak_vram_mib,
            }
        )
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TtsBenchmarkObservation:
        try:
            return cls(
                identity=_identity_from_dict(data),
                utterance_id=data["utterance_id"],
                repetition=data["repetition"],
                status=BenchmarkStatus(data["status"]),
                synthesis_latency_ms=data["synthesis_latency_ms"],
                process_cpu_ms=data["process_cpu_ms"],
                output_bytes=data["output_bytes"],
                peak_rss_mib=data.get("peak_rss_mib"),
                peak_vram_mib=data.get("peak_vram_mib"),
                error_code=data.get("error_code", ""),
                schema_version=data["schema_version"],
                record_type=data["record_type"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid TTS benchmark observation") from error


def run_asr_benchmark(
    provider: AsrProvider,
    identity: BenchmarkIdentity,
    samples: Sequence[BenchmarkAudioSample],
    *,
    repetitions: int = 1,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    process_time_ns: Callable[[], int] = time.process_time_ns,
) -> tuple[AsrBenchmarkObservation, ...]:
    _validate_run_size(len(samples), repetitions)
    observations = []
    for repetition in range(1, repetitions + 1):
        for sample in samples:
            wall_start = monotonic_ns()
            cpu_start = process_time_ns()
            try:
                transcript = provider.transcribe(sample.audio, Event())
                status = BenchmarkStatus.OK
                text = transcript.raw_text
                error_code = ""
            except Exception:
                status = BenchmarkStatus.FAILED
                text = ""
                error_code = "provider_failed"
            observations.append(
                AsrBenchmarkObservation(
                    identity=identity,
                    utterance_id=sample.utterance_id,
                    repetition=repetition,
                    status=status,
                    audio_duration_ms=sample.audio_duration_ms,
                    final_latency_ms=_elapsed_ms(wall_start, monotonic_ns()),
                    process_cpu_ms=_elapsed_ms(cpu_start, process_time_ns()),
                    transcript=text,
                    error_code=error_code,
                )
            )
    return tuple(observations)


def run_tts_benchmark(
    provider: TtsProvider,
    identity: BenchmarkIdentity,
    corpus: BenchmarkCorpus,
    *,
    repetitions: int = 1,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    process_time_ns: Callable[[], int] = time.process_time_ns,
) -> tuple[TtsBenchmarkObservation, ...]:
    _validate_run_size(len(corpus.utterances), repetitions)
    observations = []
    for repetition in range(1, repetitions + 1):
        for utterance in corpus.utterances:
            wall_start = monotonic_ns()
            cpu_start = process_time_ns()
            try:
                audio = provider.synthesize(utterance.text, Event())
                status = BenchmarkStatus.OK
                output_bytes = len(audio.data)
                error_code = ""
            except Exception:
                status = BenchmarkStatus.FAILED
                output_bytes = 0
                error_code = "provider_failed"
            observations.append(
                TtsBenchmarkObservation(
                    identity=identity,
                    utterance_id=utterance.utterance_id,
                    repetition=repetition,
                    status=status,
                    synthesis_latency_ms=_elapsed_ms(
                        wall_start, monotonic_ns()
                    ),
                    process_cpu_ms=_elapsed_ms(cpu_start, process_time_ns()),
                    output_bytes=output_bytes,
                    error_code=error_code,
                )
            )
    return tuple(observations)


def summarize_asr(
    corpus: BenchmarkCorpus,
    observations: Sequence[AsrBenchmarkObservation],
) -> dict[str, object]:
    identity = _validate_observations(observations, AsrBenchmarkObservation)
    utterances = corpus.by_id()
    successful = []
    failed = 0
    character_errors = []
    word_errors = []
    keyword_scores = []
    latencies = []
    cpu_times = []
    real_time_factors = []
    partial_latencies = []
    peak_rss = []
    peak_vram = []
    for observation in observations:
        utterance = utterances.get(observation.utterance_id)
        if utterance is None:
            raise ValueError("observation references unknown utterance")
        if observation.status is BenchmarkStatus.FAILED:
            failed += 1
            continue
        successful.append(observation)
        character_errors.append(
            character_error_rate(utterance.text, observation.transcript)
        )
        if utterance.language is BenchmarkLanguage.EN_US:
            word_errors.append(
                word_error_rate(utterance.text, observation.transcript)
            )
        keyword_scores.append(
            keyword_accuracy(utterance.keywords, observation.transcript)
        )
        latencies.append(observation.final_latency_ms)
        cpu_times.append(observation.process_cpu_ms)
        if observation.audio_duration_ms > 0:
            real_time_factors.append(
                observation.final_latency_ms / observation.audio_duration_ms
            )
        if observation.first_partial_latency_ms is not None:
            partial_latencies.append(observation.first_partial_latency_ms)
        if observation.peak_rss_mib is not None:
            peak_rss.append(observation.peak_rss_mib)
        if observation.peak_vram_mib is not None:
            peak_vram.append(observation.peak_vram_mib)
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "summary_type": "asr",
        "identity": _identity_dict(identity),
        "total": len(observations),
        "successful": len(successful),
        "failed": failed,
        "cer_mean": _mean_or_none(character_errors),
        "wer_mean": _mean_or_none(word_errors),
        "keyword_accuracy_mean": _mean_or_none(keyword_scores),
        "final_latency_ms_p50": _percentile_or_none(latencies, 50),
        "final_latency_ms_p95": _percentile_or_none(latencies, 95),
        "first_partial_latency_ms_p50": _percentile_or_none(
            partial_latencies, 50
        ),
        "process_cpu_ms_mean": _mean_or_none(cpu_times),
        "real_time_factor_mean": _mean_or_none(real_time_factors),
        "peak_rss_mib_max": max(peak_rss) if peak_rss else None,
        "peak_vram_mib_max": max(peak_vram) if peak_vram else None,
    }


def summarize_tts(
    corpus: BenchmarkCorpus,
    observations: Sequence[TtsBenchmarkObservation],
) -> dict[str, object]:
    identity = _validate_observations(observations, TtsBenchmarkObservation)
    utterance_ids = corpus.by_id()
    latencies = []
    cpu_times = []
    output_sizes = []
    failed = 0
    peak_rss = []
    peak_vram = []
    for observation in observations:
        if observation.utterance_id not in utterance_ids:
            raise ValueError("observation references unknown utterance")
        if observation.status is BenchmarkStatus.FAILED:
            failed += 1
            continue
        latencies.append(observation.synthesis_latency_ms)
        cpu_times.append(observation.process_cpu_ms)
        output_sizes.append(float(observation.output_bytes))
        if observation.peak_rss_mib is not None:
            peak_rss.append(observation.peak_rss_mib)
        if observation.peak_vram_mib is not None:
            peak_vram.append(observation.peak_vram_mib)
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "summary_type": "tts",
        "identity": _identity_dict(identity),
        "total": len(observations),
        "successful": len(latencies),
        "failed": failed,
        "synthesis_latency_ms_p50": _percentile_or_none(latencies, 50),
        "synthesis_latency_ms_p95": _percentile_or_none(latencies, 95),
        "process_cpu_ms_mean": _mean_or_none(cpu_times),
        "output_bytes_mean": _mean_or_none(output_sizes),
        "peak_rss_mib_max": max(peak_rss) if peak_rss else None,
        "peak_vram_mib_max": max(peak_vram) if peak_vram else None,
    }


def character_error_rate(reference: str, hypothesis: str) -> float:
    _validate_metric_text(reference, "reference")
    _validate_metric_text(hypothesis, "hypothesis")
    reference_units = list(_normalize_characters(reference))
    hypothesis_units = list(_normalize_characters(hypothesis))
    return _error_rate(reference_units, hypothesis_units)


def word_error_rate(reference: str, hypothesis: str) -> float:
    _validate_metric_text(reference, "reference")
    _validate_metric_text(hypothesis, "hypothesis")
    return _error_rate(_word_units(reference), _word_units(hypothesis))


def keyword_accuracy(keywords: Sequence[str], hypothesis: str) -> float:
    _validate_metric_text(hypothesis, "hypothesis")
    if len(keywords) > MAX_KEYWORDS:
        raise ValueError("too many keywords")
    if not keywords:
        return 1.0
    for keyword in keywords:
        _validate_metric_text(keyword, "keyword")
        _validate_text(keyword, "keyword")
    normalized = _normalize_keyword(hypothesis)
    matches = sum(
        1 for keyword in keywords if _normalize_keyword(keyword) in normalized
    )
    return matches / len(keywords)


def read_asr_observations(path: Path) -> tuple[AsrBenchmarkObservation, ...]:
    return tuple(
        AsrBenchmarkObservation.from_dict(item)
        for item in _read_ndjson(path, "asr_observation")
    )


def read_tts_observations(path: Path) -> tuple[TtsBenchmarkObservation, ...]:
    return tuple(
        TtsBenchmarkObservation.from_dict(item)
        for item in _read_ndjson(path, "tts_observation")
    )


def write_observations(
    stream: TextIO,
    observations: Iterable[AsrBenchmarkObservation | TtsBenchmarkObservation],
) -> int:
    count = 0
    total_bytes = 0
    for observation in observations:
        count += 1
        if count > MAX_OBSERVATIONS:
            raise ValueError("too many benchmark observations")
        encoded = json.dumps(
            observation.to_dict(), ensure_ascii=False, separators=(",", ":")
        )
        encoded_bytes = len((encoded + "\n").encode("utf-8"))
        if encoded_bytes > MAX_NDJSON_RECORD_BYTES:
            raise ValueError("benchmark observation exceeds size limit")
        total_bytes += encoded_bytes
        if total_bytes > MAX_OBSERVATION_FILE_BYTES:
            raise ValueError("benchmark observation file exceeds size limit")
        stream.write(encoded + "\n")
    stream.flush()
    return count


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m deskhelm_voice.benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("score-asr", "summarize-tts"):
        command = subparsers.add_parser(name)
        command.add_argument("--corpus", type=Path, required=True)
        command.add_argument("--observations", type=Path, required=True)
    args = parser.parse_args(argv)
    corpus = BenchmarkCorpus.load(args.corpus)
    if args.command == "score-asr":
        summary = summarize_asr(corpus, read_asr_observations(args.observations))
    else:
        summary = summarize_tts(corpus, read_tts_observations(args.observations))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def _read_ndjson(path: Path, expected_type: str) -> tuple[Mapping[str, object], ...]:
    records = []
    try:
        if path.stat().st_size > MAX_OBSERVATION_FILE_BYTES:
            raise ValueError("benchmark observation file exceeds size limit")
        with path.open("rb") as stream:
            line_number = 0
            while True:
                line = stream.readline(MAX_NDJSON_RECORD_BYTES + 1)
                if not line:
                    break
                line_number += 1
                if line_number > MAX_OBSERVATIONS:
                    raise ValueError("too many benchmark observations")
                if len(line) > MAX_NDJSON_RECORD_BYTES:
                    raise ValueError("benchmark observation exceeds size limit")
                try:
                    decoded = line.decode("utf-8")
                    data = json.loads(decoded)
                except (UnicodeError, json.JSONDecodeError) as error:
                    raise ValueError("invalid benchmark observation JSON") from error
                if not isinstance(data, Mapping):
                    raise ValueError("benchmark observation must be an object")
                if data.get("record_type") != expected_type:
                    raise ValueError("unexpected benchmark observation type")
                records.append(data)
    except OSError as error:
        raise ValueError("unable to read benchmark observations") from error
    return tuple(records)


def _identity_from_dict(data: Mapping[str, object]) -> BenchmarkIdentity:
    return BenchmarkIdentity(
        run_id=data["run_id"],
        provider_name=data["provider_name"],
        provider_version=data["provider_version"],
        model_name=data["model_name"],
        model_version=data["model_version"],
        provider_license=data["provider_license"],
        model_license=data["model_license"],
        system_profile=data["system_profile"],
        device=data["device"],
    )


def _observation_dict(
    observation: AsrBenchmarkObservation | TtsBenchmarkObservation,
) -> dict[str, object]:
    identity = observation.identity
    data = {
        "schema_version": observation.schema_version,
        "record_type": observation.record_type,
        "utterance_id": observation.utterance_id,
        "repetition": observation.repetition,
        "status": observation.status.value,
        "error_code": observation.error_code,
    }
    data.update(_identity_dict(identity))
    return data


def _identity_dict(identity: BenchmarkIdentity) -> dict[str, object]:
    return {
        "run_id": identity.run_id,
        "provider_name": identity.provider_name,
        "provider_version": identity.provider_version,
        "model_name": identity.model_name,
        "model_version": identity.model_version,
        "provider_license": identity.provider_license,
        "model_license": identity.model_license,
        "system_profile": identity.system_profile,
        "device": identity.device,
    }


def _validate_observation_common(
    schema_version: object,
    record_type: object,
    expected_type: str,
    identity: object,
    utterance_id: object,
    repetition: object,
    status: object,
    error_code: object,
) -> None:
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != BENCHMARK_SCHEMA_VERSION
    ):
        raise ValueError("unsupported benchmark observation version")
    if record_type != expected_type:
        raise ValueError("benchmark observation type is invalid")
    if not isinstance(identity, BenchmarkIdentity):
        raise ValueError("benchmark identity is invalid")
    _validate_text(utterance_id, "utterance_id")
    if (
        not isinstance(repetition, int)
        or isinstance(repetition, bool)
        or repetition < 1
    ):
        raise ValueError("repetition must be a positive integer")
    if not isinstance(status, BenchmarkStatus):
        raise ValueError("benchmark status is invalid")
    if not isinstance(error_code, str):
        raise ValueError("error_code must be a string")
    if status is BenchmarkStatus.FAILED:
        _validate_text(error_code, "error_code")
    elif error_code:
        raise ValueError("successful observation must not contain error_code")


def _validate_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"{name} must be a list of strings")
    return tuple(value)


def _validate_unique_texts(values: tuple[str, ...], name: str) -> None:
    for value in values:
        _validate_text(value, name)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


def _validate_non_negative_number(value: object, name: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be a finite non-negative number")


def _validate_optional_number(value: object, name: str) -> None:
    if value is not None:
        _validate_non_negative_number(value, name)


def _validate_metric_text(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > MAX_TEXT_CHARS:
        raise ValueError(f"{name} is too long")


def _validate_run_size(item_count: int, repetitions: int) -> None:
    if (
        not isinstance(repetitions, int)
        or isinstance(repetitions, bool)
        or repetitions < 1
    ):
        raise ValueError("repetitions must be a positive integer")
    if item_count < 1:
        raise ValueError("benchmark input must not be empty")
    if item_count * repetitions > MAX_OBSERVATIONS:
        raise ValueError("benchmark run exceeds observation limit")


def _validate_observations(
    observations: Sequence[object], observation_type: type
) -> BenchmarkIdentity:
    if not observations:
        raise ValueError("benchmark observations must not be empty")
    if len(observations) > MAX_OBSERVATIONS:
        raise ValueError("too many benchmark observations")
    if not all(isinstance(item, observation_type) for item in observations):
        raise ValueError("benchmark observation type is invalid")
    identities = {item.identity for item in observations}
    if len(identities) != 1:
        raise ValueError("benchmark observations must share one identity")
    return next(iter(identities))


def _elapsed_ms(start_ns: int, end_ns: int) -> float:
    return max(0.0, (end_ns - start_ns) / 1_000_000)


def _normalize_characters(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if not character.isspace())


def _word_units(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.findall(r"\w+|[^\w\s]", normalized, flags=re.UNICODE)


def _normalize_keyword(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _error_rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_unit in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_unit in enumerate(hypothesis, start=1):
            substitution = previous[hypothesis_index - 1] + (
                reference_unit != hypothesis_unit
            )
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    substitution,
                )
            )
        previous = current
    return previous[-1] / len(reference)


def _mean_or_none(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile_or_none(values: Sequence[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil((percentile / 100) * len(ordered)) - 1
    return ordered[max(0, rank)]


if __name__ == "__main__":
    raise SystemExit(main())
