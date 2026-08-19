#!/usr/bin/env python3
from __future__ import annotations

import argparse
from array import array
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import shlex
import sys
from threading import Event, Timer
import time

try:
    from deskhelm_voice import (
        AudioProviderKind,
        CapturedAudio,
        LocalAudioConfig,
        ParaformerStreamingAsrProvider,
        PcmChunk,
        PcmStreamFormat,
        StreamingAsrResult,
        SenseVoiceOfflineAsrProvider,
        VadEvent,
        VadEventKind,
        VoiceNoTranscript,
        WebRtcVadProvider,
        discover_pipewire_audio,
        measure_audio_signal,
    )
    from deskhelm_voice.benchmark import (
        BenchmarkCorpus,
        BenchmarkUtterance,
        character_error_rate,
        keyword_accuracy,
    )
except ModuleNotFoundError as error:
    if error.name != "deskhelm_voice":
        raise
    from voice.deskhelm_voice import (
        AudioProviderKind,
        CapturedAudio,
        LocalAudioConfig,
        ParaformerStreamingAsrProvider,
        PcmChunk,
        PcmStreamFormat,
        StreamingAsrResult,
        SenseVoiceOfflineAsrProvider,
        VadEvent,
        VadEventKind,
        VoiceNoTranscript,
        WebRtcVadProvider,
        discover_pipewire_audio,
        measure_audio_signal,
    )
    from voice.deskhelm_voice.benchmark import (
        BenchmarkCorpus,
        BenchmarkUtterance,
        character_error_rate,
        keyword_accuracy,
    )


DEFAULT_CORPUS = Path("voice/benchmarks/utterances-v1.json")
DEFAULT_UTTERANCE_ID = "zh-repeat-01"
PARAFORMER_ARTIFACTS = (
    "model.pt",
    "config.yaml",
    "tokens.json",
    "am.mvn",
    "seg_dict",
)
SENSEVOICE_ARTIFACTS = ("model.int8.onnx", "tokens.txt")
MAX_VAD_EVENTS = 256


def main() -> int:
    args = _parser().parse_args()
    _validate_args(args)
    utterance = _load_utterance(args.corpus, args.utterance_id)
    _validate_model_directory(args.asr_model_directory, args.asr_provider)
    audio_config = LocalAudioConfig(
        capture_provider=AudioProviderKind.PIPEWIRE,
        source_name=args.source,
        latency=args.latency,
        pw_cat_command_prefix=_command_prefix(args.pw_cat_command_prefix),
    )
    inventory = discover_pipewire_audio(
        pw_dump_executable=args.pw_dump_executable,
        wpctl_executable=args.wpctl_executable,
    )
    selection = audio_config.resolve(inventory)
    capture = audio_config.create_capture_provider(
        max_capture_seconds=args.capture_seconds + 2.0,
        max_capture_bytes=1 << 20,
    )
    provider = _create_asr_provider(args)
    vad_provider = (
        WebRtcVadProvider() if args.vad_provider == "webrtc" else None
    )
    print(
        f"deskhelm: speak this public diagnostic phrase: {utterance.text}",
        file=sys.stderr,
    )
    if args.lead_in_seconds:
        time.sleep(args.lead_in_seconds)
    try:
        audio = _capture_for_duration(capture, args.capture_seconds)
    except Exception:
        summary = _capture_failure_summary()
    else:
        summary = _diagnose_audio(
            audio,
            provider,
            utterance,
            vad_provider=vad_provider,
        )
    summary.update(
        {
            "source": selection.source.name,
            "source_description": selection.source.description,
            "asr_provider": args.asr_provider,
            "utterance_id": utterance.utterance_id,
        }
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["status"] == "ok" else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture one bounded public diagnostic phrase and report signal and "
            "ASR metrics without saving PCM or printing recognized text."
        )
    )
    parser.add_argument("--live-audio", action="store_true")
    parser.add_argument(
        "--asr-provider",
        choices=("paraformer", "sensevoice"),
        default="paraformer",
    )
    parser.add_argument("--asr-model-directory", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--utterance-id", default=DEFAULT_UTTERANCE_ID)
    parser.add_argument("--source")
    parser.add_argument("--latency", default="20ms")
    parser.add_argument("--pw-cat-command-prefix", default="pw-cat")
    parser.add_argument("--pw-dump-executable", default="pw-dump")
    parser.add_argument("--wpctl-executable", default="wpctl")
    parser.add_argument("--capture-seconds", type=float, default=6.0)
    parser.add_argument("--lead-in-seconds", type=float, default=0.0)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument(
        "--vad-provider",
        choices=("none", "webrtc"),
        default="webrtc",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if not args.live_audio:
        raise ValueError("--live-audio is required for microphone access")
    if not 2.0 <= args.capture_seconds <= 15.0:
        raise ValueError("capture duration must be between 2 and 15 seconds")
    if not 0.0 <= args.lead_in_seconds <= 10.0:
        raise ValueError("lead-in duration must be between 0 and 10 seconds")
    if not 1 <= args.cpu_threads <= 32:
        raise ValueError("CPU thread count must be between 1 and 32")


def _command_prefix(value: str) -> tuple[str, ...]:
    try:
        command = tuple(shlex.split(value))
    except ValueError as error:
        raise ValueError("pw-cat command prefix is invalid") from error
    if not command:
        raise ValueError("pw-cat command prefix is invalid")
    return command


def _load_utterance(path: Path, utterance_id: str) -> BenchmarkUtterance:
    corpus = BenchmarkCorpus.load(path)
    matches = [
        utterance
        for utterance in corpus.utterances
        if utterance.utterance_id == utterance_id
    ]
    if len(matches) != 1:
        raise ValueError("diagnostic utterance is unavailable")
    return matches[0]


def _validate_model_directory(path: Path, provider: str) -> None:
    if not path.is_dir():
        raise ValueError(f"{provider} model directory is unavailable")
    artifacts = (
        PARAFORMER_ARTIFACTS
        if provider == "paraformer"
        else SENSEVOICE_ARTIFACTS
    )
    for name in artifacts:
        if not (path / name).is_file():
            raise ValueError(f"{provider} {name} is unavailable")


def _create_asr_provider(args: argparse.Namespace):
    common = {
        "cpu_threads": args.cpu_threads,
        "max_audio_seconds": args.capture_seconds + 2.0,
    }
    if args.asr_provider == "sensevoice":
        return SenseVoiceOfflineAsrProvider(
            str(args.asr_model_directory),
            **common,
        )
    return ParaformerStreamingAsrProvider(
        str(args.asr_model_directory),
        **common,
    )


def _capture_for_duration(provider, seconds: float) -> CapturedAudio:
    stop = Event()
    cancel = Event()
    timer = Timer(seconds, stop.set)
    timer.daemon = True
    timer.start()
    try:
        return provider.capture(stop, cancel)
    finally:
        timer.cancel()


def _diagnose_audio(
    audio: CapturedAudio,
    provider,
    utterance: BenchmarkUtterance,
    *,
    vad_provider=None,
    monotonic_ns=time.monotonic_ns,
) -> dict[str, object]:
    summary = {
        **_signal_summary(audio),
        **_speech_activity_summary(audio, vad_provider),
        "status": "failed",
        "error_code": "",
        "transcript_chars": 0,
        "exact_match": False,
        "character_error_rate": None,
        "keyword_accuracy": None,
        "first_partial_latency_ms": None,
        "final_asr_latency_ms": None,
        "requires_post_run_speech_confirmation": True,
        "privacy": "PCM and recognized text were not saved or printed",
    }
    started_ns = monotonic_ns()
    try:
        with open(os.devnull, "w", encoding="utf-8") as private_output:
            with redirect_stdout(private_output), redirect_stderr(private_output):
                transcribe_streaming = getattr(
                    provider,
                    "transcribe_streaming",
                    None,
                )
                if callable(transcribe_streaming):
                    result = transcribe_streaming(audio, Event())
                    if not isinstance(result, StreamingAsrResult):
                        raise RuntimeError("ASR provider returned an invalid result")
                    transcript = result.transcript
                    summary["first_partial_latency_ms"] = (
                        result.first_partial_latency_ms
                    )
                else:
                    transcript = provider.transcribe(audio, Event())
    except VoiceNoTranscript:
        summary["error_code"] = "voice_no_transcript"
        summary["final_asr_latency_ms"] = _milliseconds(
            monotonic_ns() - started_ns
        )
        return summary
    except Exception:
        summary["error_code"] = "voice_asr_failed"
        summary["final_asr_latency_ms"] = _milliseconds(
            monotonic_ns() - started_ns
        )
        return summary

    hypothesis = transcript.normalized_text
    error_rate = character_error_rate(utterance.text, hypothesis)
    summary.update(
        {
            "status": "ok",
            "transcript_chars": len(hypothesis),
            "exact_match": error_rate == 0,
            "character_error_rate": round(error_rate, 6),
            "keyword_accuracy": round(
                keyword_accuracy(utterance.keywords, hypothesis),
                6,
            ),
            "final_asr_latency_ms": _milliseconds(
                monotonic_ns() - started_ns
            ),
        }
    )
    return summary


def _capture_failure_summary() -> dict[str, object]:
    return {
        "status": "failed",
        "error_code": "voice_input_failed",
        "duration_ms": None,
        "bytes_captured": 0,
        "sample_rate_hz": None,
        "channels": None,
        "sample_format": None,
        "peak_fraction": None,
        "rms_fraction": None,
        "clipped_sample_fraction": None,
        "near_silence_fraction": None,
        "input_level_hint": "unavailable",
        "input_level_hint_is_provisional": True,
        "speech_activity_status": "unavailable",
        "speech_activity_error_code": "",
        "speech_segment_count": None,
        "speech_active_ms": None,
        "speech_active_fraction": None,
        "first_speech_start_ms": None,
        "last_speech_end_ms": None,
        "transcript_chars": 0,
        "exact_match": False,
        "character_error_rate": None,
        "keyword_accuracy": None,
        "first_partial_latency_ms": None,
        "final_asr_latency_ms": None,
        "requires_post_run_speech_confirmation": True,
        "privacy": "PCM and recognized text were not saved or printed",
    }


def _speech_activity_summary(
    audio: CapturedAudio,
    provider,
) -> dict[str, object]:
    if provider is None:
        return {
            "speech_activity_status": "disabled",
            "speech_activity_error_code": "",
            "speech_segment_count": None,
            "speech_active_ms": None,
            "speech_active_fraction": None,
            "first_speech_start_ms": None,
            "last_speech_end_ms": None,
        }
    stream_format = PcmStreamFormat(
        audio.sample_rate_hz,
        channels=audio.channels,
        sample_format=audio.sample_format,
    )
    chunk = PcmChunk(audio.data, stream_format, 0)
    try:
        with open(os.devnull, "w", encoding="utf-8") as private_output:
            with redirect_stdout(private_output), redirect_stderr(private_output):
                with provider.open_session(stream_format) as session:
                    events = session.process(chunk, Event()) + session.finish(
                        Event()
                    )
        segments = _validate_vad_events(events, chunk.end_frame)
    except Exception:
        return {
            "speech_activity_status": "failed",
            "speech_activity_error_code": "voice_vad_failed",
            "speech_segment_count": None,
            "speech_active_ms": None,
            "speech_active_fraction": None,
            "first_speech_start_ms": None,
            "last_speech_end_ms": None,
        }
    active_frames = sum(end - start for start, end in segments)
    return {
        "speech_activity_status": "ok",
        "speech_activity_error_code": "",
        "speech_segment_count": len(segments),
        "speech_active_ms": round(
            active_frames * 1000 / audio.sample_rate_hz,
            3,
        ),
        "speech_active_fraction": round(active_frames / chunk.end_frame, 6),
        "first_speech_start_ms": (
            round(segments[0][0] * 1000 / audio.sample_rate_hz, 3)
            if segments
            else None
        ),
        "last_speech_end_ms": (
            round(segments[-1][1] * 1000 / audio.sample_rate_hz, 3)
            if segments
            else None
        ),
    }


def _validate_vad_events(
    events: object,
    total_frames: int,
) -> tuple[tuple[int, int], ...]:
    if not isinstance(events, tuple) or len(events) > MAX_VAD_EVENTS:
        raise ValueError("VAD events are invalid")
    segments = []
    active_start = None
    last_frame = -1
    for event in events:
        if (
            not isinstance(event, VadEvent)
            or event.frame_index < last_frame
            or event.frame_index > total_frames
        ):
            raise ValueError("VAD event is invalid")
        if active_start is None:
            if event.kind is not VadEventKind.SPEECH_STARTED:
                raise ValueError("VAD event order is invalid")
            active_start = event.frame_index
        else:
            if (
                event.kind is not VadEventKind.SPEECH_ENDED
                or event.frame_index <= active_start
            ):
                raise ValueError("VAD event order is invalid")
            segments.append((active_start, event.frame_index))
            active_start = None
        last_frame = event.frame_index
    if active_start is not None:
        raise ValueError("VAD speech segment is incomplete")
    return tuple(segments)


def _signal_summary(audio: CapturedAudio) -> dict[str, object]:
    report = measure_audio_signal(audio)
    samples = array("h")
    samples.frombytes(audio.data)
    if sys.byteorder != "little":
        samples.byteswap()
    clipped_fraction = sum(abs(sample) >= 32760 for sample in samples) / len(samples)
    near_silence_fraction = sum(abs(sample) <= 32 for sample in samples) / len(samples)
    if report.peak_fraction >= 0.999 or clipped_fraction >= 0.001:
        level_hint = "possible_clipping"
    elif report.rms_fraction < 0.005 and report.peak_fraction < 0.05:
        level_hint = "too_quiet"
    elif report.rms_fraction < 0.015:
        level_hint = "low"
    else:
        level_hint = "within_diagnostic_range"
    return {
        "duration_ms": round(report.duration_ms, 3),
        "bytes_captured": report.bytes_captured,
        "sample_rate_hz": report.sample_rate_hz,
        "channels": report.channels,
        "sample_format": report.sample_format,
        "peak_fraction": round(report.peak_fraction, 6),
        "rms_fraction": round(report.rms_fraction, 6),
        "clipped_sample_fraction": round(clipped_fraction, 8),
        "near_silence_fraction": round(near_silence_fraction, 6),
        "input_level_hint": level_hint,
        "input_level_hint_is_provisional": True,
    }


def _milliseconds(nanoseconds: int) -> float:
    if not isinstance(nanoseconds, int) or isinstance(nanoseconds, bool):
        raise ValueError("duration must be integer nanoseconds")
    if nanoseconds < 0:
        raise ValueError("duration must not be negative")
    return round(nanoseconds / 1_000_000, 3)


if __name__ == "__main__":
    raise SystemExit(main())
