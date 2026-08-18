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
    load_prepared_asr_set,
)
from deskhelm_voice.benchmark import (
    BenchmarkIdentity,
    run_asr_benchmark,
    summarize_asr,
    write_observations,
)


MODEL_REVISION = "fd2af606b37d7fb8b3b8a218c5be5b07b53ef6ba"
MODEL_SHA256 = "4fdfb48ed4471777c9a511e96a2acae17f77cac9d709cc756634622769192a64"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args()
    verify_start = time.monotonic_ns()
    _verify_model(args.model_directory)
    model_verification_ms = (time.monotonic_ns() - verify_start) / 1_000_000
    corpus, samples = load_prepared_asr_set(args.manifest, args.prepared)
    provider = ParaformerStreamingAsrProvider(
        str(args.model_directory),
        cpu_threads=args.cpu_threads,
    )
    load_start = time.monotonic_ns()
    provider.load()
    model_load_ms = (time.monotonic_ns() - load_start) / 1_000_000
    identity = BenchmarkIdentity.create(
        provider_name="funasr",
        provider_version=importlib.metadata.version("funasr"),
        model_name=(
            "paraformer-zh-streaming chunk=[0,10,5] "
            "encoder-look-back=4 decoder-look-back=1"
        ),
        model_version=f"apache-2.0-20260804+{MODEL_REVISION}",
        provider_license="MIT",
        model_license="Apache-2.0",
        system_profile=(
            f"{platform.system().lower()}-{platform.machine()}-"
            f"python{platform.python_version()}"
        ),
        device=f"cpu-{args.cpu_threads}-threads",
    )
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
            "model_sha256": MODEL_SHA256,
            "model_verification_ms": model_verification_ms,
            "model_load_ms": model_load_ms,
            "process_peak_rss_mib": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss
            / 1024,
            "first_partial_latency_basis": (
                "audio available at first non-empty increment plus that "
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


def _verify_model(model_directory: Path) -> None:
    model_path = model_directory / "model.pt"
    try:
        if model_path.stat().st_size > 1 << 30:
            raise ValueError("Paraformer model exceeds size limit")
        digest = hashlib.sha256()
        with model_path.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
        checksum = digest.hexdigest()
    except OSError as error:
        raise ValueError("unable to read Paraformer model") from error
    if checksum != MODEL_SHA256:
        raise ValueError("Paraformer model checksum is invalid")


if __name__ == "__main__":
    raise SystemExit(main())
