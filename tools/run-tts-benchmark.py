#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Callable
import importlib.metadata
import json
from pathlib import Path
import platform
import resource
from threading import Event, Thread
import time
import wave

from deskhelm_voice import KokoroTtsProvider, PiperTtsProvider, VoiceCancelled
from deskhelm_voice.models import SynthesizedAudio
from deskhelm_voice.providers import StreamingTtsProvider
from deskhelm_voice.benchmark import (
    BenchmarkCorpus,
    BenchmarkIdentity,
    run_tts_benchmark,
    summarize_tts,
    write_observations,
)
from deskhelm_voice.tts_manifest import TtsCandidate, TtsCandidateManifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--audio-directory", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args()
    manifest = TtsCandidateManifest.load(args.manifest)
    candidate = manifest.candidate(args.candidate)
    corpus = BenchmarkCorpus.load(args.corpus)
    _verify_runtime(candidate.provider_name, candidate.provider_version)
    provider = _create_provider(
        candidate.candidate_id,
        candidate,
        args.prepared,
        args.cpu_threads,
    )
    load_start = time.monotonic_ns()
    provider.load()
    model_load_ms = (time.monotonic_ns() - load_start) / 1_000_000
    identity = BenchmarkIdentity.create(
        provider_name=candidate.provider_name,
        provider_version=candidate.provider_version,
        model_name=candidate.model_name,
        model_version=f"{candidate.model_version}+{candidate.model_revision}",
        provider_license=candidate.provider_license,
        model_license=candidate.model_license,
        system_profile=(
            f"{platform.system().lower()}-{platform.machine()}-"
            f"python{platform.python_version()}"
        ),
        device=f"cpu-{args.cpu_threads}-threads",
    )
    observations = run_tts_benchmark(
        provider,
        identity,
        corpus,
        repetitions=args.repetitions,
        audio_consumer=_audio_writer(args.audio_directory),
    )
    args.observations.parent.mkdir(parents=True, exist_ok=True)
    with args.observations.open("w", encoding="utf-8") as stream:
        write_observations(stream, observations)
    interruption = _measure_interruption(
        provider,
        _interruption_text(corpus),
    )
    summary = summarize_tts(corpus, observations)
    summary.update(
        {
            "candidate_id": candidate.candidate_id,
            "model_load_ms": model_load_ms,
            "process_peak_rss_mib": (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            ),
            "interruption_probe": interruption,
            "streaming_latency_basis": (
                "provider call to first complete provider audio chunk"
            ),
        }
    )
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "candidate": candidate.candidate_id,
                "total": summary["total"],
                "failed": summary["failed"],
                "summary": str(args.summary),
            }
        )
    )
    return 0


def _create_provider(
    candidate_id: str,
    candidate: TtsCandidate,
    prepared: Path,
    cpu_threads: int,
) -> PiperTtsProvider | KokoroTtsProvider:
    if candidate_id == "piper-chaowen-medium":
        return PiperTtsProvider(
            str(candidate.artifact_path("model", prepared)),
            str(candidate.artifact_path("config", prepared)),
            str(prepared / candidate.candidate_id),
            cpu_threads=cpu_threads,
        )
    if candidate_id == "kokoro-v1-auto-zh-en":
        return KokoroTtsProvider(
            str(candidate.artifact_path("config", prepared)),
            str(candidate.artifact_path("model", prepared)),
            str(candidate.artifact_path("chinese_voice", prepared)),
            str(candidate.artifact_path("english_voice", prepared)),
            cpu_threads=cpu_threads,
        )
    raise ValueError("unsupported TTS candidate")


def _verify_runtime(provider_name: str, expected_version: str) -> None:
    try:
        actual = importlib.metadata.version(provider_name)
    except importlib.metadata.PackageNotFoundError as error:
        raise RuntimeError("TTS runtime is unavailable") from error
    if actual != expected_version:
        raise RuntimeError("TTS runtime version does not match manifest")


def _audio_writer(
    directory: Path | None,
) -> Callable[[str, int, SynthesizedAudio], None] | None:
    if directory is None:
        return None
    directory.mkdir(parents=True, exist_ok=True)

    def write(
        utterance_id: str,
        repetition: int,
        audio: SynthesizedAudio,
    ) -> None:
        if repetition != 1:
            return
        path = directory / f"{utterance_id}.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(audio.channels)
            stream.setsampwidth(audio.sample_format.bytes_per_sample)
            stream.setframerate(audio.sample_rate_hz)
            stream.writeframes(audio.data)

    return write


def _interruption_text(corpus: BenchmarkCorpus) -> str:
    longest = max(corpus.utterances, key=lambda item: len(item.text)).text
    return " ".join(longest for _ in range(8))[:4096]


def _measure_interruption(
    provider: StreamingTtsProvider,
    text: str,
) -> dict[str, object]:
    cancel = Event()
    first_audio = Event()
    finished = Event()
    result = {"status": "running", "chunks_before_cancel": 0}

    def run() -> None:
        try:
            for _chunk in provider.synthesize_streaming(text, cancel):
                result["chunks_before_cancel"] += 1
                first_audio.set()
            result["status"] = "completed"
        except VoiceCancelled:
            result["status"] = "cancelled"
        except Exception:
            result["status"] = "provider_failed"
        finally:
            finished.set()

    worker = Thread(target=run, name="tts-interruption-probe", daemon=True)
    worker.start()
    if not first_audio.wait(timeout=60):
        cancel.set()
        finished.wait(timeout=60)
        return {
            "status": "no_first_audio",
            "latency_ms": None,
            "chunks_before_cancel": result["chunks_before_cancel"],
        }
    if finished.is_set():
        return {
            "status": "completed_before_cancel",
            "latency_ms": None,
            "chunks_before_cancel": result["chunks_before_cancel"],
        }
    cancel_start = time.monotonic_ns()
    cancel.set()
    if not finished.wait(timeout=60):
        return {
            "status": "timeout",
            "latency_ms": None,
            "chunks_before_cancel": result["chunks_before_cancel"],
        }
    latency_ms = (time.monotonic_ns() - cancel_start) / 1_000_000
    return {
        "status": result["status"],
        "latency_ms": latency_ms,
        "chunks_before_cancel": result["chunks_before_cancel"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
