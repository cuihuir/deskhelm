#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
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
        )

    samples = []
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
            source_pcm = converted[part.source_id]
            start_frame = len(pcm) // frame_bytes
            pcm.extend(source_pcm)
            reference_segments.append(
                {
                    "start_frame": start_frame,
                    "end_frame": len(pcm) // frame_bytes,
                }
            )
        total_frames = len(pcm) // frame_bytes
        padding_frames = (-total_frames) % chunk_frames
        pcm.extend(bytes(padding_frames * frame_bytes))
        file_name = f"{scenario.scenario_id}.wav"
        output_path = prepared_directory / file_name
        _write_wav(
            output_path,
            bytes(pcm),
            manifest.sample_rate_hz,
            manifest.channels,
        )
        samples.append(
            {
                "sample_id": scenario.scenario_id,
                "file_name": file_name,
                "sha256": _sha256(output_path.read_bytes()),
                "total_frames": len(pcm) // frame_bytes,
                "reference_segments": reference_segments,
            }
        )
    index = {
        "schema_version": 1,
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


def _convert(ffmpeg: str, path: Path, sample_rate_hz: int, channels: int) -> bytes:
    result = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate_hz),
            "-ac",
            str(channels),
            "pipe:1",
        ],
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
    return result.stdout


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
