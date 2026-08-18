#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import platform

from deskhelm_voice import SileroOnnxVadProvider, WebRtcVadProvider
from deskhelm_voice.benchmark import (
    BenchmarkIdentity,
    run_vad_benchmark,
    summarize_vad,
    write_observations,
)
from deskhelm_voice.vad_samples import load_prepared_vad_samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("webrtc", "silero"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--silero-model", type=Path)
    args = parser.parse_args()
    samples = load_prepared_vad_samples(args.manifest, args.prepared)
    system_profile = (
        f"{platform.system().lower()}-{platform.machine()}-"
        f"python{platform.python_version()}"
    )
    if args.provider == "webrtc":
        provider = WebRtcVadProvider()
        identity = BenchmarkIdentity.create(
            provider_name="webrtcvad-wheels",
            provider_version=importlib.metadata.version("webrtcvad-wheels"),
            model_name="WebRTC VAD mode=2 frame=20ms start=3/5 end=8/10",
            model_version="e283ca41df3a84b0e87fb1f5cb9b21580a286b09",
            provider_license="MIT",
            model_license="BSD-3-Clause",
            system_profile=system_profile,
            device="cpu",
        )
    else:
        if args.silero_model is None:
            parser.error("--silero-model is required for Silero")
        provider = SileroOnnxVadProvider(str(args.silero_model))
        identity = BenchmarkIdentity.create(
            provider_name="onnxruntime",
            provider_version=importlib.metadata.version("onnxruntime"),
            model_name="Silero VAD threshold=0.5 negative=0.35 silence=100ms pad=30ms",
            model_version="6.2.1+7e30209a3e901f9842f81b225f3e93d8199902b1",
            provider_license="MIT",
            model_license="MIT",
            system_profile=system_profile,
            device="cpu",
        )
    # Candidate initialization is separated from per-stream replay latency.
    with provider.open_session(samples[0].format):
        pass
    observations = run_vad_benchmark(
        provider,
        identity,
        samples,
        repetitions=args.repetitions,
    )
    args.observations.parent.mkdir(parents=True, exist_ok=True)
    with args.observations.open("w", encoding="utf-8") as stream:
        write_observations(stream, observations)
    print(json.dumps(summarize_vad(observations), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
