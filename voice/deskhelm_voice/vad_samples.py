from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import wave

from .benchmark import MAX_VAD_SAMPLE_BYTES, VadBenchmarkSample
from .models import PcmSampleFormat
from .streaming import PcmChunk, PcmStreamFormat, SpeechSegment
from .vad_manifest import MAX_MANIFEST_BYTES, VadRunManifest


def load_prepared_vad_samples(
    manifest_path: Path,
    prepared_directory: Path,
) -> tuple[VadBenchmarkSample, ...]:
    manifest = VadRunManifest.load(manifest_path)
    index_path = prepared_directory / "index.json"
    try:
        if index_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("prepared VAD index exceeds size limit")
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("unable to read prepared VAD index") from error
    if (
        not isinstance(index, Mapping)
        or index.get("schema_version") != 1
        or index.get("manifest_name") != manifest.name
        or index.get("dataset_revision") != manifest.dataset_revision
    ):
        raise ValueError("prepared VAD index does not match manifest")
    entries = index.get("samples")
    if not isinstance(entries, list) or not all(
        isinstance(entry, Mapping) for entry in entries
    ):
        raise ValueError("prepared VAD sample index is invalid")
    sample_ids = [entry.get("sample_id") for entry in entries]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("prepared VAD sample IDs must be unique")
    by_id = {
        entry.get("sample_id"): entry
        for entry in entries
        if isinstance(entry, Mapping)
    }
    samples = []
    for scenario in manifest.scenarios:
        entry = by_id.get(scenario.scenario_id)
        if not isinstance(entry, Mapping):
            raise ValueError("prepared VAD sample is missing")
        file_name = entry.get("file_name")
        if not isinstance(file_name, str) or Path(file_name).name != file_name:
            raise ValueError("prepared VAD file name is invalid")
        pcm = _read_pcm_wav(
            prepared_directory / file_name,
            manifest.sample_rate_hz,
            manifest.channels,
            entry.get("sha256"),
        )
        if entry.get("total_frames") != len(pcm) // (manifest.channels * 2):
            raise ValueError("prepared VAD frame count is invalid")
        raw_segments = entry.get("reference_segments")
        if not isinstance(raw_segments, list):
            raise ValueError("prepared VAD reference segments are invalid")
        try:
            segments = tuple(
                SpeechSegment(item["start_frame"], item["end_frame"])
                for item in raw_segments
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("prepared VAD reference segments are invalid") from error
        if len(segments) != sum(
            part.source_id is not None for part in scenario.parts
        ):
            raise ValueError("prepared VAD reference count is invalid")
        stream_format = PcmStreamFormat(
            sample_rate_hz=manifest.sample_rate_hz,
            channels=manifest.channels,
            sample_format=PcmSampleFormat.S16LE,
        )
        chunk_frames = manifest.sample_rate_hz * manifest.chunk_ms // 1000
        chunks = tuple(
            PcmChunk(
                pcm[
                    start * stream_format.frame_bytes :
                    (start + chunk_frames) * stream_format.frame_bytes
                ],
                stream_format,
                start,
            )
            for start in range(
                0,
                len(pcm) // stream_format.frame_bytes,
                chunk_frames,
            )
        )
        samples.append(VadBenchmarkSample(scenario.scenario_id, chunks, segments))
    return tuple(samples)


def _read_pcm_wav(
    path: Path,
    sample_rate_hz: int,
    channels: int,
    expected_sha256: object,
) -> bytes:
    try:
        if path.stat().st_size > MAX_VAD_SAMPLE_BYTES + 4096:
            raise ValueError("prepared VAD WAV exceeds size limit")
        payload = path.read_bytes()
        if (
            not isinstance(expected_sha256, str)
            or hashlib.sha256(payload).hexdigest() != expected_sha256
        ):
            raise ValueError("prepared VAD WAV checksum is invalid")
        with wave.open(str(path), "rb") as stream:
            if (
                stream.getcomptype() != "NONE"
                or stream.getsampwidth() != 2
                or stream.getframerate() != sample_rate_hz
                or stream.getnchannels() != channels
            ):
                raise ValueError("prepared VAD WAV format is invalid")
            pcm = stream.readframes(stream.getnframes())
    except (OSError, EOFError, wave.Error) as error:
        raise ValueError("unable to read prepared VAD WAV") from error
    frame_bytes = channels * 2
    if not pcm or len(pcm) > MAX_VAD_SAMPLE_BYTES or len(pcm) % frame_bytes:
        raise ValueError("prepared VAD PCM is invalid")
    return pcm
