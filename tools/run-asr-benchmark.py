#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import resource
import time

from deskhelm_voice import (
    ParaformerStreamingAsrProvider,
    SenseVoiceOfflineAsrProvider,
    load_prepared_asr_set,
)
from deskhelm_voice.benchmark import (
    BenchmarkIdentity,
    run_asr_benchmark,
    summarize_asr,
    write_observations,
)


PARAFORMER_REVISION = "fd2af606b37d7fb8b3b8a218c5be5b07b53ef6ba"
PARAFORMER_SHA256 = (
    "4fdfb48ed4471777c9a511e96a2acae17f77cac9d709cc756634622769192a64"
)
SENSEVOICE_ASSET_ID = "288366523"
SENSEVOICE_MODEL_SHA256 = (
    "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"
)
SENSEVOICE_TOKENS_SHA256 = (
    "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--provider",
        choices=("paraformer", "sensevoice"),
        default="paraformer",
    )
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args()
    verify_start = time.monotonic_ns()
    checksums = _verify_model(args.model_directory, args.provider)
    model_verification_ms = (time.monotonic_ns() - verify_start) / 1_000_000
    corpus, samples = load_prepared_asr_set(args.manifest, args.prepared)
    provider = _create_provider(args)
    load_start = time.monotonic_ns()
    provider.load()
    model_load_ms = (time.monotonic_ns() - load_start) / 1_000_000
    identity = _benchmark_identity(args.provider, args.cpu_threads)
    observations = run_asr_benchmark(
        provider,
        identity,
        samples,
        repetitions=args.repetitions,
    )
    args.observations.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with args.observations.open("w", encoding="utf-8") as stream:
        write_observations(stream, observations)
    summary = summarize_asr(corpus, observations)
    summary.update(
        {
            **checksums,
            "model_verification_ms": model_verification_ms,
            "model_load_ms": model_load_ms,
            "process_peak_rss_mib": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss
            / 1024,
            "first_partial_latency_basis": (
                "final-only; unavailable"
                if args.provider == "sensevoice"
                else "audio available at first non-empty increment plus that "
                "chunk's offline processing time"
            ),
        }
    )
    args.summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "total": summary["total"],
                "failed": summary["failed"],
                "summary": str(args.summary),
            }
        )
    )
    return 0


def _create_provider(args: argparse.Namespace):
    if args.provider == "sensevoice":
        return SenseVoiceOfflineAsrProvider(
            str(args.model_directory),
            cpu_threads=args.cpu_threads,
        )
    return ParaformerStreamingAsrProvider(
        str(args.model_directory),
        cpu_threads=args.cpu_threads,
    )


def _benchmark_identity(provider: str, cpu_threads: int) -> BenchmarkIdentity:
    common = {
        "system_profile": (
            f"{platform.system().lower()}-{platform.machine()}-"
            f"python{platform.python_version()}"
        ),
        "device": f"cpu-{cpu_threads}-threads",
    }
    if provider == "sensevoice":
        return BenchmarkIdentity.create(
            provider_name="sherpa-onnx",
            provider_version=importlib.metadata.version("sherpa-onnx"),
            model_name="sensevoice-small-int8 auto-language use-itn",
            model_version=f"2024-07-17+asset-{SENSEVOICE_ASSET_ID}",
            provider_license="Apache-2.0",
            model_license="FunASR-Model-License-1.1",
            **common,
        )
    return BenchmarkIdentity.create(
        provider_name="funasr",
        provider_version=importlib.metadata.version("funasr"),
        model_name=(
            "paraformer-zh-streaming chunk=[0,10,5] "
            "encoder-look-back=4 decoder-look-back=1"
        ),
        model_version=f"apache-2.0-20260804+{PARAFORMER_REVISION}",
        provider_license="MIT",
        model_license="Apache-2.0",
        **common,
    )


def _verify_model(
    model_directory: Path,
    provider: str,
) -> dict[str, str]:
    if provider == "sensevoice":
        model_checksum = _verify_file(
            model_directory / "model.int8.onnx",
            SENSEVOICE_MODEL_SHA256,
            1 << 29,
            "SenseVoice model",
        )
        tokens_checksum = _verify_file(
            model_directory / "tokens.txt",
            SENSEVOICE_TOKENS_SHA256,
            1 << 20,
            "SenseVoice tokens",
        )
        return {
            "model_sha256": model_checksum,
            "tokens_sha256": tokens_checksum,
        }
    return {
        "model_sha256": _verify_file(
            model_directory / "model.pt",
            PARAFORMER_SHA256,
            1 << 30,
            "Paraformer model",
        )
    }


def _verify_file(
    path: Path,
    expected_checksum: str,
    max_bytes: int,
    label: str,
) -> str:
    try:
        if path.stat().st_size > max_bytes:
            raise ValueError(f"{label} exceeds size limit")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
        checksum = digest.hexdigest()
    except OSError as error:
        raise ValueError(f"unable to read {label}") from error
    if checksum != expected_checksum:
        raise ValueError(f"{label} checksum is invalid")
    return checksum


if __name__ == "__main__":
    raise SystemExit(main())
