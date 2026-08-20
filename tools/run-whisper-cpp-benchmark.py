#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import platform
import resource
import time

try:
    from deskhelm_voice import WhisperCppAsrProvider, load_prepared_asr_set
    from deskhelm_voice.benchmark import (
        BenchmarkIdentity,
        run_asr_benchmark,
        summarize_asr,
        write_observations,
    )
except ModuleNotFoundError as error:
    if error.name != "deskhelm_voice":
        raise
    from voice.deskhelm_voice import WhisperCppAsrProvider, load_prepared_asr_set
    from voice.deskhelm_voice.benchmark import (
        BenchmarkIdentity,
        run_asr_benchmark,
        summarize_asr,
        write_observations,
    )


MODEL_METADATA = {
    "base": {
        "size_bytes": 147_951_465,
        "upstream_sha1": "465707469ff3a37a2b9b8d8f89f2f99de7299dac",
        "source_url": (
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
            "ggml-base.bin"
        ),
    },
    "small": {
        "size_bytes": 487_601_967,
        "upstream_sha1": "55356645c2b361a969dfd0ef2c5a50d530afd8d5",
        "source_url": (
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
            "ggml-small.bin"
        ),
    },
}
SHIM_ABI = "deskhelm_whisper_shim_v1"


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _validate_args(args)
    metadata = _verify_model(args.model_path, args.model)
    if not args.library_path.is_file():
        raise ValueError("whisper.cpp shim library is unavailable")
    corpus, samples = load_prepared_asr_set(args.manifest, args.prepared)
    provider = WhisperCppAsrProvider(
        str(args.library_path),
        str(args.model_path),
        cpu_threads=args.cpu_threads,
        language=args.language,
        max_audio_seconds=120.0,
    )
    load_start = time.monotonic_ns()
    provider.load()
    model_load_ms = (time.monotonic_ns() - load_start) / 1_000_000
    try:
        observations = run_asr_benchmark(
            provider,
            _benchmark_identity(args.model, args.cpu_threads, metadata),
            samples,
            repetitions=args.repetitions,
        )
    finally:
        provider.close()
    peak_rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    observations = tuple(
        replace(observation, peak_rss_mib=peak_rss_mib)
        for observation in observations
    )
    args.observations.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with args.observations.open("w", encoding="utf-8") as stream:
        write_observations(stream, observations)
    summary = summarize_asr(corpus, observations)
    summary.update(
        {
            "model_load_ms": model_load_ms,
            "model_name": args.model,
            "model_size_bytes": metadata["size_bytes"],
            "model_source_url": metadata["source_url"],
            "model_upstream_sha1": metadata["upstream_sha1"],
            "model_sha256": metadata["sha256"],
            "shim_abi": SHIM_ABI,
            "shim_library_path": str(args.library_path),
            "language": args.language,
            "repetitions": args.repetitions,
            "cpu_threads": args.cpu_threads,
        }
    )
    args.summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "model": args.model,
                "total": summary["total"],
                "successful": summary["successful"],
                "failed": summary["failed"],
                "summary": str(args.summary),
            },
            sort_keys=True,
        )
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=tuple(MODEL_METADATA), required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--library-path", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("voice/benchmarks/asr-external-v1.json"),
    )
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--language", default="auto")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.repetitions <= 3:
        raise ValueError("repetitions must be between 1 and 3")
    if not 1 <= args.cpu_threads <= 32:
        raise ValueError("CPU thread count must be between 1 and 32")
    if args.language != "auto" and not args.language.isascii():
        raise ValueError("language must be ASCII or auto")


def _verify_model(path: Path, model: str) -> dict[str, object]:
    metadata = MODEL_METADATA[model]
    if not path.is_file():
        raise ValueError("whisper.cpp model is unavailable")
    if path.stat().st_size != metadata["size_bytes"]:
        raise ValueError("whisper.cpp model size does not match pinned artifact")
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            sha1.update(chunk)
            sha256.update(chunk)
    if sha1.hexdigest() != metadata["upstream_sha1"]:
        raise ValueError("whisper.cpp model SHA-1 does not match pinned artifact")
    return {
        **metadata,
        "sha256": sha256.hexdigest(),
    }


def _benchmark_identity(
    model: str,
    cpu_threads: int,
    metadata: dict[str, object],
) -> BenchmarkIdentity:
    return BenchmarkIdentity.create(
        provider_name="whisper.cpp-shim",
        provider_version="v1.9.2/deskhelm_whisper_shim_v1",
        model_name=f"whisper.cpp multilingual {model}",
        model_version=str(metadata["upstream_sha1"]),
        provider_license="MIT",
        model_license="MIT",
        system_profile=(
            f"{platform.system().lower()}-{platform.machine()}-"
            f"python{platform.python_version()}"
        ),
        device=f"cpu-{cpu_threads}-threads",
    )


if __name__ == "__main__":
    raise SystemExit(main())
