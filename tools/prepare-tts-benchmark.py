#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tarfile
import tempfile
import urllib.request

from deskhelm_voice.tts_manifest import TtsCandidateManifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = TtsCandidateManifest.load(args.manifest)
    prepared = args.artifact_root / "prepared"
    prepared.mkdir(parents=True, exist_ok=True)
    count = 0
    for candidate in manifest.candidates:
        directory = prepared / candidate.candidate_id
        directory.mkdir(parents=True, exist_ok=True)
        for artifact in candidate.artifacts:
            path = directory / artifact.file_name
            if not _matches(path, artifact.size_bytes, artifact.sha256):
                _download(path, artifact.url, artifact.size_bytes, artifact.sha256)
            if artifact.role == "g2pw_archive":
                _extract_g2pw(path, directory / "g2pW")
            count += 1
    print(f"prepared {count} TTS artifacts in {prepared}")
    return 0


def _download(path: Path, url: str, size_bytes: int, checksum: str) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DeskHelm-TTS-Benchmark/1"},
    )
    digest = hashlib.sha256()
    total = 0
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                while chunk := response.read(1 << 20):
                    total += len(chunk)
                    if total > size_bytes:
                        raise ValueError("TTS artifact exceeds expected size")
                    digest.update(chunk)
                    temporary.write(chunk)
            if total != size_bytes or digest.hexdigest() != checksum:
                raise ValueError("TTS artifact verification failed")
            temporary.flush()
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


def _matches(path: Path, size_bytes: int, checksum: str) -> bool:
    try:
        if path.stat().st_size != size_bytes:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
        return digest.hexdigest() == checksum
    except OSError:
        return False


def _extract_g2pw(archive: Path, destination: Path) -> None:
    required = {
        "g2pw.onnx",
        "config.py",
        "POLYPHONIC_CHARS.txt",
        "MONOPHONIC_CHARS.txt",
    }
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    count = 0
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        for member in members:
            count += 1
            total += member.size
            member_path = Path(member.name)
            if (
                count > 128
                or total > 256 << 20
                or member.islnk()
                or member.issym()
                or member_path.is_absolute()
                or ".." in member_path.parts
            ):
                raise ValueError("G2PW archive is unsafe")
        stream.extractall(destination, members=members, filter="data")
    if not required.issubset({path.name for path in destination.iterdir()}):
        raise ValueError("G2PW archive is incomplete")


if __name__ == "__main__":
    raise SystemExit(main())
