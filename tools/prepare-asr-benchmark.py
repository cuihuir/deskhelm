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

from deskhelm_voice.asr_manifest import (
    AsrRunManifest,
    MAX_ASR_SOURCE_BYTES,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="/usr/bin/ffmpeg")
    args = parser.parse_args()
    manifest = AsrRunManifest.load(args.manifest)
    raw_directory = args.artifact_root / "raw"
    prepared_directory = args.artifact_root / "prepared"
    raw_directory.mkdir(parents=True, exist_ok=True)
    prepared_directory.mkdir(parents=True, exist_ok=True)
    samples = []
    for source in manifest.sources:
        raw_path = raw_directory / source.file_name
        valid_existing = (
            raw_path.exists()
            and raw_path.stat().st_size <= MAX_ASR_SOURCE_BYTES
            and _sha256(raw_path.read_bytes()) == source.sha256
        )
        if not valid_existing:
            payload = _download(source.url)
            if _sha256(payload) != source.sha256:
                raise ValueError(f"checksum mismatch for {source.utterance_id}")
            raw_path.write_bytes(payload)
        pcm = _convert(
            args.ffmpeg,
            raw_path,
            manifest.sample_rate_hz,
            manifest.channels,
        )
        file_name = f"{source.utterance_id}.wav"
        output_path = prepared_directory / file_name
        _write_wav(
            output_path,
            pcm,
            manifest.sample_rate_hz,
            manifest.channels,
        )
        samples.append(
            {
                "utterance_id": source.utterance_id,
                "file_name": file_name,
                "sha256": _sha256(output_path.read_bytes()),
                "audio_duration_ms": len(pcm)
                * 1000
                / (manifest.sample_rate_hz * manifest.channels * 2),
            }
        )
    index = {
        "schema_version": 1,
        "manifest_name": manifest.name,
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
        headers={"User-Agent": "DeskHelm-ASR-Benchmark/1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(MAX_ASR_SOURCE_BYTES + 1)
    if not payload or len(payload) > MAX_ASR_SOURCE_BYTES:
        raise ValueError("ASR download size is invalid")
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
        or len(result.stdout) > MAX_ASR_SOURCE_BYTES
    ):
        raise ValueError("ASR audio conversion failed")
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
