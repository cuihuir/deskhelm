#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shlex
from threading import Condition
import time

try:
    from deskhelm_voice import (
        AudioProviderKind,
        LocalAsrProviderKind,
        LocalAudioConfig,
        LocalTtsProviderKind,
        LocalVoiceConfig,
        SpeechItem,
        VoiceEvent,
        VoiceEventKind,
        VoiceTarget,
        discover_pipewire_audio,
    )
except ModuleNotFoundError as error:
    if error.name != "deskhelm_voice":
        raise
    from voice.deskhelm_voice import (
        AudioProviderKind,
        LocalAsrProviderKind,
        LocalAudioConfig,
        LocalTtsProviderKind,
        LocalVoiceConfig,
        SpeechItem,
        VoiceEvent,
        VoiceEventKind,
        VoiceTarget,
        discover_pipewire_audio,
    )


DEFAULT_RESPONSE_TEXT = "DeskHelm 本地语音链路测试完成。"


@dataclass(frozen=True, slots=True)
class _TimedEvent:
    event: VoiceEvent
    occurred_ns: int


def main() -> int:
    args = _parser().parse_args()
    _validate_args(args)
    audio = LocalAudioConfig(
        capture_provider=AudioProviderKind.PIPEWIRE,
        playback_provider=AudioProviderKind.PIPEWIRE,
        source_name=args.source,
        sink_name=args.sink,
        latency=args.latency,
        pw_cat_command_prefix=_command_prefix(args.pw_cat_command_prefix),
    )
    config = LocalVoiceConfig(
        audio=audio,
        asr_provider=LocalAsrProviderKind.PARAFORMER,
        asr_model_directory=args.asr_model_directory,
        tts_provider=LocalTtsProviderKind.PIPER,
        tts_model_path=args.tts_model,
        tts_config_path=args.tts_config,
        tts_resource_directory=args.tts_resource_directory,
        cpu_threads=args.cpu_threads,
        max_capture_seconds=max(30.0, args.capture_seconds + 5.0),
    )
    inventory = discover_pipewire_audio(
        pw_dump_executable=args.pw_dump_executable,
        wpctl_executable=args.wpctl_executable,
    )
    composition = config.compose(inventory)
    gateway = composition.gateway
    condition = Condition()
    events: list[_TimedEvent] = []
    transcript_chars = 0

    def observe(event: VoiceEvent) -> None:
        with condition:
            events.append(_TimedEvent(event, time.monotonic_ns()))
            condition.notify_all()

    gateway.event_sink = observe
    target = VoiceTarget("local-voice-smoke", "live-1", "deskhelm")

    def accept_transcript(_target, transcript) -> None:
        nonlocal transcript_chars
        transcript_chars = len(transcript.normalized_text)
        gateway.enqueue_speech(
            SpeechItem(
                target=target,
                text=args.response_text,
                speech_id="local-voice-smoke-response",
            )
        )

    gateway.register_prompt_sink(accept_transcript)
    run_started_ns = time.monotonic_ns()
    release_ns = 0
    try:
        gateway.press_ptt(target, activation_id="local-voice-smoke-press")
        time.sleep(args.capture_seconds)
        release_ns = time.monotonic_ns()
        if not gateway.release_ptt(
            target,
            activation_id="local-voice-smoke-press",
        ):
            raise RuntimeError("live PTT release did not match active capture")
        deadline = time.monotonic() + args.timeout_seconds
        with condition:
            condition.wait_for(
                lambda: _is_terminal(events),
                timeout=max(0.0, deadline - time.monotonic()),
            )
        summary = _summarize(
            events,
            run_started_ns=run_started_ns,
            release_ns=release_ns,
            transcript_chars=transcript_chars,
            source_name=composition.audio_selection.source.name,
            sink_name=composition.audio_selection.sink.name,
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["status"] == "ok" else 1
    finally:
        gateway.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Explicitly capture live audio, transcribe it, synthesize a fixed "
            "response, and play it without saving PCM or transcript text."
        )
    )
    parser.add_argument("--live-audio", action="store_true")
    parser.add_argument("--asr-model-directory", type=Path, required=True)
    parser.add_argument("--tts-model", type=Path, required=True)
    parser.add_argument("--tts-config", type=Path, required=True)
    parser.add_argument("--tts-resource-directory", type=Path, required=True)
    parser.add_argument("--source")
    parser.add_argument("--sink")
    parser.add_argument("--latency", default="20ms")
    parser.add_argument("--pw-cat-command-prefix", default="pw-cat")
    parser.add_argument("--pw-dump-executable", default="pw-dump")
    parser.add_argument("--wpctl-executable", default="wpctl")
    parser.add_argument("--capture-seconds", type=float, default=4.0)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--response-text", default=DEFAULT_RESPONSE_TEXT)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if not args.live_audio:
        raise ValueError("--live-audio is required for microphone and speaker access")
    if not 2.0 <= args.capture_seconds <= 15.0:
        raise ValueError("capture duration must be between 2 and 15 seconds")
    if not 10.0 <= args.timeout_seconds <= 300.0:
        raise ValueError("timeout must be between 10 and 300 seconds")
    if not 1 <= args.cpu_threads <= 32:
        raise ValueError("CPU thread count must be between 1 and 32")
    if not isinstance(args.response_text, str) or not args.response_text.strip():
        raise ValueError("response text must not be empty")
    if len(args.response_text) > 256:
        raise ValueError("response text exceeds live diagnostic limit")


def _command_prefix(value: str) -> tuple[str, ...]:
    try:
        command = tuple(shlex.split(value))
    except ValueError as error:
        raise ValueError("pw-cat command prefix is invalid") from error
    if not command:
        raise ValueError("pw-cat command prefix is invalid")
    return command


def _is_terminal(events: list[_TimedEvent]) -> bool:
    return any(
        item.event.kind
        in {
            VoiceEventKind.SPEECH_COMPLETED,
            VoiceEventKind.FAILURE,
        }
        for item in events
    )


def _summarize(
    events: list[_TimedEvent],
    *,
    run_started_ns: int,
    release_ns: int,
    transcript_chars: int,
    source_name: str,
    sink_name: str,
) -> dict[str, object]:
    times: dict[VoiceEventKind, int] = {}
    failure_code = ""
    for item in events:
        times.setdefault(item.event.kind, item.occurred_ns)
        if item.event.kind is VoiceEventKind.FAILURE:
            failure_code = item.event.error_code
    completed_ns = times.get(VoiceEventKind.SPEECH_COMPLETED)
    failure_ns = times.get(VoiceEventKind.FAILURE)
    terminal_ns = completed_ns or failure_ns
    status = "ok" if completed_ns is not None else "failed"
    if terminal_ns is None:
        status = "timeout"
        failure_code = "live_voice_timeout"
        terminal_ns = time.monotonic_ns()
    summary: dict[str, object] = {
        "status": status,
        "failure_code": failure_code,
        "source": source_name,
        "sink": sink_name,
        "transcript_chars": transcript_chars,
        "event_sequence": [item.event.kind.value for item in events],
        "total_ms": _milliseconds(terminal_ns - run_started_ns),
        "first_audio_measured": False,
        "speech_started_semantics": "before TTS synthesis",
        "privacy": "PCM and transcript text were not saved or printed",
    }
    _add_delta(
        summary,
        "release_to_transcribing_ms",
        times,
        release_ns,
        VoiceEventKind.TRANSCRIBING,
    )
    _add_delta(
        summary,
        "release_to_transcript_ms",
        times,
        release_ns,
        VoiceEventKind.TRANSCRIPT_READY,
    )
    transcript_ns = times.get(VoiceEventKind.TRANSCRIPT_READY)
    if transcript_ns is not None:
        _add_delta(
            summary,
            "transcript_to_speech_started_ms",
            times,
            transcript_ns,
            VoiceEventKind.SPEECH_STARTED,
        )
    speech_started_ns = times.get(VoiceEventKind.SPEECH_STARTED)
    if speech_started_ns is not None:
        _add_delta(
            summary,
            "speech_start_to_complete_ms",
            times,
            speech_started_ns,
            VoiceEventKind.SPEECH_COMPLETED,
        )
    return summary


def _add_delta(
    summary: dict[str, object],
    field_name: str,
    times: dict[VoiceEventKind, int],
    start_ns: int,
    end_kind: VoiceEventKind,
) -> None:
    end_ns = times.get(end_kind)
    if end_ns is not None:
        summary[field_name] = _milliseconds(end_ns - start_ns)


def _milliseconds(duration_ns: int) -> float:
    return round(max(0, duration_ns) / 1_000_000, 3)


if __name__ == "__main__":
    raise SystemExit(main())
