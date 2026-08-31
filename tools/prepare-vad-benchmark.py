#!/usr/bin/env python3
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import wave

from deskhelm_voice.vad_manifest import VadRunManifest


MAX_DOWNLOAD_BYTES = 4 << 20
MAX_CONVERTED_BYTES = 8 << 20


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="/usr/bin/ffmpeg")
    args = parser.parse_args()
    manifest = VadRunManifest.load(args.manifest)
    raw_directory = args.artifact_root / "raw"
    prepared_directory = args.artifact_root / "prepared"
    raw_directory.mkdir(parents=True, exist_ok=True)
    prepared_directory.mkdir(parents=True, exist_ok=True)

    converted: dict[str, bytes] = {}
    for source in manifest.sources:
        raw_path = raw_directory / source.file_name
        valid_existing = (
            raw_path.exists()
            and raw_path.stat().st_size <= MAX_DOWNLOAD_BYTES
            and _sha256(raw_path.read_bytes()) == source.sha256
        )
        if not valid_existing:
            payload = _download(source.url)
            if _sha256(payload) != source.sha256:
                raise ValueError(f"checksum mismatch for {source.source_id}")
            raw_path.write_bytes(payload)
        converted[source.source_id] = _convert(
            args.ffmpeg,
            raw_path,
            manifest.sample_rate_hz,
            manifest.channels,
            source.clip_start_ms,
            source.clip_duration_ms,
        )

    samples = []
    sources_by_id = {source.source_id: source for source in manifest.sources}
    frame_bytes = manifest.channels * 2
    chunk_frames = manifest.sample_rate_hz * manifest.chunk_ms // 1000
    for scenario in manifest.scenarios:
        pcm = bytearray()
        reference_segments = []
        for part in scenario.parts:
            if part.silence_ms is not None:
                silence_frames = manifest.sample_rate_hz * part.silence_ms // 1000
                pcm.extend(bytes(silence_frames * frame_bytes))
                continue
            source = sources_by_id[part.source_id]
            source_pcm = converted[part.source_id]
            start_frame = len(pcm) // frame_bytes
            pcm.extend(source_pcm)
            if source.speech_segments_ms:
                for segment in source.speech_segments_ms:
                    reference_segments.append(
                        {
                            "start_frame": start_frame
                            + _milliseconds_to_frames(
                                segment.start_ms,
                                manifest.sample_rate_hz,
                            ),
                            "end_frame": start_frame
                            + _milliseconds_to_frames(
                                segment.end_ms,
                                manifest.sample_rate_hz,
                            ),
                        }
                    )
            else:
                reference_segments.append(
                    {
                        "start_frame": start_frame,
                        "end_frame": len(pcm) // frame_bytes,
                    }
                )
        total_frames = len(pcm) // frame_bytes
        padding_frames = (-total_frames) % chunk_frames
        pcm.extend(bytes(padding_frames * frame_bytes))
        mix_scale = 1.0
        if scenario.noise_source_id is not None:
            pcm_bytes, mix_scale = _mix_background(
                bytes(pcm),
                converted[scenario.noise_source_id],
                reference_segments,
                scenario.snr_db,
            )
        else:
            pcm_bytes = bytes(pcm)
        file_name = f"{scenario.scenario_id}.wav"
        output_path = prepared_directory / file_name
        _write_wav(
            output_path,
            pcm_bytes,
            manifest.sample_rate_hz,
            manifest.channels,
        )
        samples.append(
            {
                "sample_id": scenario.scenario_id,
                "file_name": file_name,
                "sha256": _sha256(output_path.read_bytes()),
                "total_frames": len(pcm_bytes) // frame_bytes,
                "reference_segments": reference_segments,
                "noise_source_id": scenario.noise_source_id,
                "snr_db": scenario.snr_db,
                "mix_scale": round(mix_scale, 9),
            }
        )
    index = {
        "schema_version": manifest.schema_version,
        "manifest_name": manifest.name,
        "dataset_revision": manifest.dataset_revision,
        "samples": samples,
    }
    (prepared_directory / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"prepared": len(samples), "directory": str(prepared_directory)}
        )
    )
    return 0


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DeskHelm-VAD-Benchmark/1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(MAX_DOWNLOAD_BYTES + 1)
    if not payload or len(payload) > MAX_DOWNLOAD_BYTES:
        raise ValueError("download size is invalid")
    return payload


def _convert(
    ffmpeg: str,
    path: Path,
    sample_rate_hz: int,
    channels: int,
    clip_start_ms: int,
    clip_duration_ms: int | None,
) -> bytes:
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
    ]
    if clip_start_ms:
        command.extend(("-ss", f"{clip_start_ms / 1000:.3f}"))
    if clip_duration_ms is not None:
        command.extend(("-t", f"{clip_duration_ms / 1000:.3f}"))
    command.extend(
        (
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate_hz),
            "-ac",
            str(channels),
            "pipe:1",
        )
    )
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        timeout=30,
    )
    if (
        result.returncode
        or not result.stdout
        or len(result.stdout) > MAX_CONVERTED_BYTES
    ):
        raise ValueError("audio conversion failed")
    if clip_duration_ms is not None:
        expected_bytes = (
            _milliseconds_to_frames(clip_duration_ms, sample_rate_hz)
            * channels
            * 2
        )
        if len(result.stdout) != expected_bytes:
            raise ValueError("audio clip duration is invalid")
    return result.stdout


def _mix_background(
    speech_pcm: bytes,
    noise_pcm: bytes,
    reference_segments: list[dict[str, int]],
    snr_db: int | None,
) -> tuple[bytes, float]:
    if snr_db is None or not speech_pcm or not noise_pcm:
        raise ValueError("noise mix configuration is invalid")
    speech = _samples(speech_pcm)
    noise = _samples(noise_pcm)
    reference = []
    for segment in reference_segments:
        reference.extend(speech[segment["start_frame"] : segment["end_frame"]])
    speech_rms = _rms(reference)
    noise_rms = _rms(noise)
    if speech_rms <= 0 or noise_rms <= 0:
        raise ValueError("noise mix signal is invalid")
    noise_gain = speech_rms / (noise_rms * math.pow(10.0, snr_db / 20.0))
    mixed = [
        sample + noise[index % len(noise)] * noise_gain
        for index, sample in enumerate(speech)
    ]
    peak = max(abs(sample) for sample in mixed)
    mix_scale = min(1.0, 32760.0 / peak) if peak else 1.0
    output = array(
        "h",
        (
            max(-32768, min(32767, round(sample * mix_scale)))
            for sample in mixed
        ),
    )
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes(), mix_scale


def _samples(pcm: bytes) -> array:
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _rms(samples) -> float:
    if not samples:
        return 0.0
    return math.sqrt(math.fsum(sample * sample for sample in samples) / len(samples))


def _milliseconds_to_frames(milliseconds: int, sample_rate_hz: int) -> int:
    return round(milliseconds * sample_rate_hz / 1000)


def _write_wav(path: Path, pcm: bytes, sample_rate_hz: int, channels: int) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with wave.open(str(temporary_path), "wb") as stream:
            stream.setnchannels(channels)
            stream.setsampwidth(2)
            stream.setframerate(sample_rate_hz)
            stream.writeframes(pcm)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
