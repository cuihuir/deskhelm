#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.metadata
import json
from pathlib import Path
import platform
from typing import Sequence

from deskhelm_voice import SileroOnnxVadProvider, WebRtcVadProvider
from deskhelm_voice.benchmark import (
    BenchmarkIdentity,
    MAX_OBSERVATIONS,
    run_vad_benchmark,
    summarize_vad,
    write_observations,
)
from deskhelm_voice.vad_samples import load_prepared_vad_samples


WEBRTC_SWEEP_MODES = (0, 1, 2, 3)
SILERO_SWEEP_THRESHOLDS = (
    (0.35, 0.2),
    (0.5, 0.35),
    (0.65, 0.5),
)


@dataclass(frozen=True, slots=True)
class VadProfile:
    profile_id: str
    webrtc_mode: int | None = None
    silero_threshold: float | None = None
    silero_negative_threshold: float | None = None
    silero_minimum_silence_ms: int = 100
    silero_speech_pad_ms: int = 30


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("webrtc", "silero"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--silero-model", type=Path)
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--webrtc-mode", type=int, choices=range(4))
    parser.add_argument("--silero-threshold", type=float)
    parser.add_argument("--silero-negative-threshold", type=float)
    parser.add_argument("--silero-minimum-silence-ms", type=int)
    parser.add_argument("--silero-speech-pad-ms", type=int)
    parser.add_argument("--summary", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    profiles = _profile_specs(args, parser)
    samples = load_prepared_vad_samples(args.manifest, args.prepared)
    if len(profiles) * len(samples) * args.repetitions > MAX_OBSERVATIONS:
        parser.error("VAD sweep exceeds the observation limit")
    system_profile = (
        f"{platform.system().lower()}-{platform.machine()}-"
        f"python{platform.python_version()}"
    )
    observations = []
    summaries = []
    for profile in profiles:
        provider, identity = _build_profile(args, profile, system_profile)
        # Candidate initialization is separated from per-stream replay latency.
        with provider.open_session(samples[0].format):
            pass
        profile_observations = run_vad_benchmark(
            provider,
            identity,
            samples,
            repetitions=args.repetitions,
        )
        observations.extend(profile_observations)
        profile_summary = summarize_vad(profile_observations)
        profile_summary["profile_id"] = profile.profile_id
        summaries.append(profile_summary)
    args.observations.parent.mkdir(parents=True, exist_ok=True)
    with args.observations.open("w", encoding="utf-8") as stream:
        write_observations(stream, observations)
    if len(summaries) == 1:
        summary = summaries[0]
    else:
        summary = {
            "schema_version": 1,
            "summary_type": "vad_sweep",
            "provider": args.provider,
            "profiles": summaries,
        }
    encoded_summary = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.summary is not None:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(encoded_summary, encoding="utf-8")
    print(encoded_summary, end="")
    return 0


def _profile_specs(args, parser: argparse.ArgumentParser) -> tuple[VadProfile, ...]:
    explicit = (
        args.webrtc_mode,
        args.silero_threshold,
        args.silero_negative_threshold,
        args.silero_minimum_silence_ms,
        args.silero_speech_pad_ms,
    )
    if args.sweep and any(value is not None for value in explicit):
        parser.error("--sweep cannot be combined with explicit VAD parameters")
    if args.provider == "webrtc":
        if args.silero_model is not None or any(
            value is not None for value in explicit[1:]
        ):
            parser.error("Silero options require --provider silero")
        modes = (
            WEBRTC_SWEEP_MODES
            if args.sweep
            else (
                args.webrtc_mode if args.webrtc_mode is not None else 2,
            )
        )
        return tuple(
            VadProfile(profile_id=f"webrtc-mode-{mode}", webrtc_mode=mode)
            for mode in modes
        )
    if args.silero_model is None:
        parser.error("--silero-model is required for Silero")
    if args.webrtc_mode is not None:
        parser.error("--webrtc-mode requires --provider webrtc")
    minimum_silence_ms = (
        args.silero_minimum_silence_ms
        if args.silero_minimum_silence_ms is not None
        else 100
    )
    speech_pad_ms = (
        args.silero_speech_pad_ms
        if args.silero_speech_pad_ms is not None
        else 30
    )
    if args.sweep:
        thresholds = SILERO_SWEEP_THRESHOLDS
    else:
        thresholds = (
            (
                (
                    args.silero_threshold
                    if args.silero_threshold is not None
                    else 0.5
                ),
                (
                    args.silero_negative_threshold
                    if args.silero_negative_threshold is not None
                    else 0.35
                ),
            ),
        )
    return tuple(
        VadProfile(
            profile_id=f"silero-{threshold:g}-{negative:g}",
            silero_threshold=threshold,
            silero_negative_threshold=negative,
            silero_minimum_silence_ms=minimum_silence_ms,
            silero_speech_pad_ms=speech_pad_ms,
        )
        for threshold, negative in thresholds
    )


def _build_profile(args, profile: VadProfile, system_profile: str):
    if args.provider == "webrtc":
        assert profile.webrtc_mode is not None
        provider = WebRtcVadProvider(mode=profile.webrtc_mode)
        identity = BenchmarkIdentity.create(
            provider_name="webrtcvad-wheels",
            provider_version=importlib.metadata.version("webrtcvad-wheels"),
            model_name=(
                f"WebRTC VAD mode={profile.webrtc_mode} frame=20ms "
                "start=3/5 end=8/10"
            ),
            model_version="e283ca41df3a84b0e87fb1f5cb9b21580a286b09",
            provider_license="MIT",
            model_license="BSD-3-Clause",
            system_profile=system_profile,
            device="cpu",
        )
        return provider, identity
    assert args.silero_model is not None
    assert profile.silero_threshold is not None
    assert profile.silero_negative_threshold is not None
    provider = SileroOnnxVadProvider(
        str(args.silero_model),
        threshold=profile.silero_threshold,
        negative_threshold=profile.silero_negative_threshold,
        minimum_silence_ms=profile.silero_minimum_silence_ms,
        speech_pad_ms=profile.silero_speech_pad_ms,
    )
    identity = BenchmarkIdentity.create(
        provider_name="onnxruntime",
        provider_version=importlib.metadata.version("onnxruntime"),
        model_name=(
            f"Silero VAD threshold={profile.silero_threshold:g} "
            f"negative={profile.silero_negative_threshold:g} "
            f"silence={profile.silero_minimum_silence_ms}ms "
            f"pad={profile.silero_speech_pad_ms}ms"
        ),
        model_version="6.2.1+7e30209a3e901f9842f81b225f3e93d8199902b1",
        provider_license="MIT",
        model_license="MIT",
        system_profile=system_profile,
        device="cpu",
    )
    return provider, identity


if __name__ == "__main__":
    raise SystemExit(main())
