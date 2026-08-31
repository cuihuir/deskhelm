#!/usr/bin/env python3
from __future__ import annotations

import argparse
from array import array
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import json
import math
import os
from pathlib import Path
import shlex
import selectors
import sys
from threading import Event, Thread, Timer
import time
from typing import Callable, Sequence

from deskhelm_voice import (
    LocalAudioConfig,
    SileroOnnxVadProvider,
    VadEvent,
    VadEventKind,
    WebRtcVadProvider,
    discover_pipewire_audio,
)
from deskhelm_voice.benchmark import BenchmarkCorpus


MAX_CAPTURE_SECONDS = 30.0
MAX_CAPTURE_BYTES = 2 << 20
MAX_CHUNKS = 100_000
MAX_VAD_EVENTS = 256
MAX_READY_TIMEOUT_SECONDS = 120.0
MAX_READY_LINE_LENGTH = 32
DEFAULT_CORPUS = Path("voice/benchmarks/utterances-v1.json")
DEFAULT_UTTERANCE_ID = "zh-repeat-01"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-audio", action="store_true")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--provider", choices=("webrtc", "silero"), default="webrtc")
    parser.add_argument("--source")
    parser.add_argument("--pw-cat-command-prefix", default="pw-cat")
    parser.add_argument("--pipewire-latency", default="20ms")
    parser.add_argument("--webrtc-mode", type=int, choices=range(4), default=2)
    parser.add_argument("--silero-model", type=Path)
    parser.add_argument("--silero-threshold", type=float, default=0.5)
    parser.add_argument("--silero-negative-threshold", type=float, default=0.35)
    parser.add_argument("--silero-minimum-silence-ms", type=int, default=100)
    parser.add_argument("--silero-speech-pad-ms", type=int, default=30)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--utterance-id", default=DEFAULT_UTTERANCE_ID)
    parser.add_argument(
        "--await-phrase-ready",
        action="store_true",
        help="wait for a separate 'ready' line before capture",
    )
    parser.add_argument(
        "--phrase-ready-timeout-seconds",
        type=float,
        default=60.0,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        _validate_args(args)
        utterance = _load_public_phrase(args.corpus, args.utterance_id)
        command_prefix = _command_prefix(args.pw_cat_command_prefix)
        audio_config = LocalAudioConfig(
            source_name=args.source,
            latency=args.pipewire_latency,
            pw_cat_command_prefix=command_prefix,
        )
        selection = audio_config.resolve(discover_pipewire_audio())
        capture_provider = audio_config.create_capture_provider(
            max_capture_seconds=args.seconds + 2.0,
            max_capture_bytes=MAX_CAPTURE_BYTES,
        )
        vad_provider, provider_config = _vad_provider(args)
        print(
            f"deskhelm: speak diagnostic phrase: {utterance.text}",
            file=sys.stderr,
        )
        if args.await_phrase_ready:
            print("deskhelm: reply ready to start this capture", file=sys.stderr)
        if args.await_phrase_ready and not _await_phrase_ready(
            args.phrase_ready_timeout_seconds
        ):
            summary = _failure_summary(
                args.provider,
                error_code="voice_vad_phrase_not_ready",
            )
        else:
            with open(os.devnull, "w", encoding="utf-8") as private_output:
                with redirect_stdout(private_output), redirect_stderr(private_output):
                    summary = run_live_vad_diagnostic(
                        capture_provider,
                        vad_provider,
                        seconds=args.seconds,
                    )
        summary.update(
            {
                "provider": args.provider,
                "provider_config": provider_config,
                "source_name": selection.source.name,
                "source_uses_default": selection.source_uses_default,
                "utterance_id": utterance.utterance_id,
                "phrase_ready_mode": (
                    "chat_handshake" if args.await_phrase_ready else "immediate"
                ),
                "requires_post_run_speech_confirmation": True,
                "post_run_confirmation_scope": "this capture",
            }
        )
    except Exception:
        summary = _failure_summary(args.provider)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["status"] == "ok" else 1


def run_live_vad_diagnostic(
    capture_provider,
    vad_provider,
    *,
    seconds: float,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    timer_factory=Timer,
) -> dict[str, object]:
    if not 0.5 <= seconds <= MAX_CAPTURE_SECONDS:
        raise ValueError("live VAD duration is invalid")
    stop = Event()
    cancel = Event()
    timer = timer_factory(seconds, stop.set)
    chunk_count = 0
    bytes_captured = 0
    first_chunk_ns = None
    previous_chunk_ns = None
    chunk_intervals_ms = []
    processing_ms = []
    event_records = []
    all_events = []
    signal_sample_count = 0
    signal_sum_squares = 0
    signal_peak_sample = 0
    signal_clipped_samples = 0
    signal_near_silence_samples = 0
    expected_frame = 0
    stream_format = None
    capture_started_ns = 0
    capture_finished_ns = 0

    try:
        with ExitStack() as stack:
            stream = stack.enter_context(capture_provider.open_stream())
            capture_started_ns = monotonic_ns()
            timer.start()
            session = None
            while True:
                chunk = stream.read(stop, cancel)
                if chunk is None:
                    break
                arrived_ns = monotonic_ns()
                if first_chunk_ns is None:
                    first_chunk_ns = arrived_ns
                    stream_format = chunk.format
                    session = stack.enter_context(
                        vad_provider.open_session(stream_format)
                    )
                else:
                    assert previous_chunk_ns is not None
                    chunk_intervals_ms.append(
                        _milliseconds(arrived_ns - previous_chunk_ns)
                    )
                if chunk.format != stream_format or chunk.start_frame != expected_frame:
                    raise ValueError("live PipeWire chunks are invalid")
                previous_chunk_ns = arrived_ns
                expected_frame = chunk.end_frame
                chunk_count += 1
                bytes_captured += len(chunk.data)
                if chunk_count > MAX_CHUNKS or bytes_captured > MAX_CAPTURE_BYTES:
                    raise ValueError("live PipeWire capture exceeds bounds")
                (
                    chunk_samples,
                    chunk_sum_squares,
                    chunk_peak_sample,
                    chunk_clipped_samples,
                    chunk_near_silence_samples,
                ) = _pcm_signal_totals(chunk.data)
                signal_sample_count += chunk_samples
                signal_sum_squares += chunk_sum_squares
                signal_peak_sample = max(signal_peak_sample, chunk_peak_sample)
                signal_clipped_samples += chunk_clipped_samples
                signal_near_silence_samples += chunk_near_silence_samples
                process_started_ns = monotonic_ns()
                assert session is not None
                events = session.process(chunk, cancel)
                process_finished_ns = monotonic_ns()
                latency_ms = _milliseconds(process_finished_ns - process_started_ns)
                processing_ms.append(latency_ms)
                _record_events(
                    events,
                    process_finished_ns,
                    capture_started_ns,
                    latency_ms,
                    stream_format.sample_rate_hz,
                    event_records,
                    all_events,
                )
            if session is None or stream_format is None or first_chunk_ns is None:
                raise RuntimeError("live PipeWire capture produced no audio")
            process_started_ns = monotonic_ns()
            events = session.finish(cancel)
            process_finished_ns = monotonic_ns()
            latency_ms = _milliseconds(process_finished_ns - process_started_ns)
            processing_ms.append(latency_ms)
            _record_events(
                events,
                process_finished_ns,
                capture_started_ns,
                latency_ms,
                stream_format.sample_rate_hz,
                event_records,
                all_events,
            )
            capture_finished_ns = monotonic_ns()
    finally:
        timer.cancel()

    segments = _validate_events(tuple(all_events), expected_frame)
    audio_duration_ms = expected_frame * 1000 / stream_format.sample_rate_hz
    processing_total_ms = sum(processing_ms)
    summary = {
        "status": "ok",
        "error_code": "",
        "audio_duration_ms": round(audio_duration_ms, 3),
        "capture_wall_ms": _milliseconds(capture_finished_ns - capture_started_ns),
        "first_chunk_latency_ms": _milliseconds(first_chunk_ns - capture_started_ns),
        "chunk_count": chunk_count,
        "bytes_captured": bytes_captured,
        "chunk_interarrival_ms_p50": _percentile(chunk_intervals_ms, 50),
        "chunk_interarrival_ms_p95": _percentile(chunk_intervals_ms, 95),
        "vad_processing_ms_total": round(processing_total_ms, 3),
        "vad_processing_ms_p50": _percentile(processing_ms, 50),
        "vad_processing_ms_p95": _percentile(processing_ms, 95),
        "vad_real_time_factor": round(
            processing_total_ms / audio_duration_ms,
            9,
        ),
        "speech_segment_count": len(segments),
        "speech_active_ms": round(
            sum(end - start for start, end in segments)
            * 1000
            / stream_format.sample_rate_hz,
            3,
        ),
        "event_count": len(all_events),
        "events": event_records,
        "privacy": "PCM was processed in memory and was not saved or printed",
        "timing_scope": (
            "PipeWire process handoff and VAD software timing only; "
            "not acoustic onset latency"
        ),
    }
    summary.update(
        _signal_summary(
            sample_count=signal_sample_count,
            sum_squares=signal_sum_squares,
            peak_sample=signal_peak_sample,
            clipped_samples=signal_clipped_samples,
            near_silence_samples=signal_near_silence_samples,
        )
    )
    return summary


def _record_events(
    events,
    observed_ns: int,
    capture_started_ns: int,
    process_latency_ms: float,
    sample_rate_hz: int,
    records: list[dict[str, object]],
    all_events: list[VadEvent],
) -> None:
    if not isinstance(events, tuple) or len(all_events) + len(events) > MAX_VAD_EVENTS:
        raise ValueError("live VAD event output is invalid")
    for event in events:
        if not isinstance(event, VadEvent):
            raise ValueError("live VAD event output is invalid")
        all_events.append(event)
        records.append(
            {
                "kind": event.kind.value,
                "audio_frame_index": event.frame_index,
                "audio_ms": round(event.frame_index * 1000 / sample_rate_hz, 3),
                "observed_from_capture_start_ms": _milliseconds(
                    observed_ns - capture_started_ns
                ),
                "vad_process_ms": process_latency_ms,
            }
        )


def _validate_events(
    events: tuple[VadEvent, ...], total_frames: int
) -> tuple[tuple[int, int], ...]:
    segments = []
    active_start = None
    last_frame = -1
    for event in events:
        if event.frame_index < last_frame or event.frame_index > total_frames:
            raise ValueError("live VAD event frame is invalid")
        if active_start is None:
            if event.kind is not VadEventKind.SPEECH_STARTED:
                raise ValueError("live VAD event order is invalid")
            active_start = event.frame_index
        else:
            if (
                event.kind is not VadEventKind.SPEECH_ENDED
                or event.frame_index <= active_start
            ):
                raise ValueError("live VAD event order is invalid")
            segments.append((active_start, event.frame_index))
            active_start = None
        last_frame = event.frame_index
    if active_start is not None:
        raise ValueError("live VAD active segment is incomplete")
    return tuple(segments)


def _vad_provider(args) -> tuple[object, dict[str, object]]:
    if args.provider == "webrtc":
        provider = WebRtcVadProvider(mode=args.webrtc_mode)
        return provider, {
            "mode": args.webrtc_mode,
            "frame_ms": 20,
            "start": "3/5",
            "end": "8/10",
        }
    assert args.silero_model is not None
    provider = SileroOnnxVadProvider(
        str(args.silero_model),
        threshold=args.silero_threshold,
        negative_threshold=args.silero_negative_threshold,
        minimum_silence_ms=args.silero_minimum_silence_ms,
        speech_pad_ms=args.silero_speech_pad_ms,
    )
    return provider, {
        "threshold": args.silero_threshold,
        "negative_threshold": args.silero_negative_threshold,
        "minimum_silence_ms": args.silero_minimum_silence_ms,
        "speech_pad_ms": args.silero_speech_pad_ms,
    }


def _validate_args(args) -> None:
    if not args.live_audio:
        raise ValueError("--live-audio is required")
    if (
        not isinstance(args.seconds, (int, float))
        or isinstance(args.seconds, bool)
        or not math.isfinite(args.seconds)
        or not 0.5 <= args.seconds <= MAX_CAPTURE_SECONDS
    ):
        raise ValueError("live VAD duration is invalid")
    if args.provider == "silero":
        if args.silero_model is None or not args.silero_model.is_file():
            raise ValueError("Silero model is unavailable")
    if (
        not isinstance(args.phrase_ready_timeout_seconds, (int, float))
        or isinstance(args.phrase_ready_timeout_seconds, bool)
        or not math.isfinite(args.phrase_ready_timeout_seconds)
        or not 1.0
        <= args.phrase_ready_timeout_seconds
        <= MAX_READY_TIMEOUT_SECONDS
    ):
        raise ValueError("phrase-ready timeout must be between 1 and 120 seconds")


def _pcm_signal_totals(pcm: bytes) -> tuple[int, int, int, int, int]:
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return (
        len(samples),
        sum(sample * sample for sample in samples),
        max((abs(sample) for sample in samples), default=0),
        sum(abs(sample) >= 32760 for sample in samples),
        sum(abs(sample) <= 32 for sample in samples),
    )


def _signal_summary(
    *,
    sample_count: int,
    sum_squares: int,
    peak_sample: int,
    clipped_samples: int,
    near_silence_samples: int,
) -> dict[str, object]:
    if sample_count <= 0:
        raise ValueError("live PipeWire capture produced no signal samples")
    peak_fraction = peak_sample / 32768
    rms_fraction = math.sqrt(sum_squares / sample_count) / 32768
    clipped_fraction = clipped_samples / sample_count
    near_silence_fraction = near_silence_samples / sample_count
    if peak_fraction >= 0.999 or clipped_fraction >= 0.001:
        level_hint = "possible_clipping"
    elif rms_fraction < 0.005 and peak_fraction < 0.05:
        level_hint = "too_quiet"
    elif rms_fraction < 0.015:
        level_hint = "low"
    else:
        level_hint = "within_diagnostic_range"
    return {
        "signal_peak_fraction": round(peak_fraction, 6),
        "signal_rms_fraction": round(rms_fraction, 6),
        "clipped_sample_fraction": round(clipped_fraction, 8),
        "near_silence_fraction": round(near_silence_fraction, 6),
        "input_level_hint": level_hint,
        "input_level_hint_is_provisional": True,
    }


def _load_public_phrase(path: Path, utterance_id: str):
    corpus = BenchmarkCorpus.load(path)
    matches = [
        utterance
        for utterance in corpus.utterances
        if utterance.utterance_id == utterance_id
    ]
    if len(matches) != 1:
        raise ValueError("diagnostic utterance is unavailable")
    return matches[0]


def _await_phrase_ready(timeout_seconds: float) -> bool:
    stdin = sys.stdin
    try:
        stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return _read_ready_line_with_timeout(stdin, timeout_seconds)

    selector = selectors.DefaultSelector()
    try:
        try:
            selector.register(stdin, selectors.EVENT_READ)
        except (OSError, ValueError):
            return _read_ready_line_with_timeout(stdin, timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                return False
            line = stdin.readline(MAX_READY_LINE_LENGTH)
            if not line:
                return False
            if line.strip().lower() == "ready":
                return True
    finally:
        selector.close()


def _read_ready_line_with_timeout(stdin: object, timeout_seconds: float) -> bool:
    line: list[str] = []
    complete = Event()

    def read_once() -> None:
        try:
            value = stdin.readline(MAX_READY_LINE_LENGTH)
        except Exception:
            value = ""
        if isinstance(value, str):
            line.append(value)
        complete.set()

    Thread(target=read_once, daemon=True).start()
    if not complete.wait(timeout_seconds):
        return False
    return bool(line) and line[0].strip().lower() == "ready"


def _command_prefix(value: str) -> tuple[str, ...]:
    try:
        command = tuple(shlex.split(value))
    except ValueError as error:
        raise ValueError("pw-cat command prefix is invalid") from error
    if not command or len(command) > 16 or any(len(part) > 512 for part in command):
        raise ValueError("pw-cat command prefix is invalid")
    return command


def _failure_summary(
    provider: str,
    *,
    error_code: str = "voice_vad_diagnostic_failed",
) -> dict[str, object]:
    return {
        "status": "failed",
        "error_code": error_code,
        "provider": provider,
        "audio_duration_ms": None,
        "capture_wall_ms": None,
        "first_chunk_latency_ms": None,
        "chunk_count": 0,
        "bytes_captured": 0,
        "speech_segment_count": None,
        "event_count": 0,
        "events": [],
        "signal_peak_fraction": None,
        "signal_rms_fraction": None,
        "clipped_sample_fraction": None,
        "near_silence_fraction": None,
        "input_level_hint": None,
        "input_level_hint_is_provisional": True,
        "privacy": "PCM was not saved or printed",
    }


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = rank - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def _milliseconds(nanoseconds: int) -> float:
    if nanoseconds < 0:
        raise ValueError("duration is invalid")
    return round(nanoseconds / 1_000_000, 3)


if __name__ == "__main__":
    raise SystemExit(main())
