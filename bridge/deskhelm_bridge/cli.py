from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shlex
import sys
from threading import Event
import time
from typing import Any

from .client import send_event
from .event import AgentEvent, AgentState, ProtocolError
from .paths import default_socket_path
from .server import run_bridge


def add_socket_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--socket", type=Path, default=default_socket_path())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deskhelm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bridge = subparsers.add_parser("bridge", help="run the local status bridge")
    add_socket_argument(bridge)
    bridge.add_argument("--slots", type=int, default=4)
    bridge.add_argument("--plain", action="store_true", help="print one line per event")
    bridge.add_argument("--no-color", action="store_true")
    bridge.add_argument("--max-events", type=int)
    bridge.add_argument("--max-connections", type=int, default=16)
    bridge.add_argument("--max-subscribers", type=int)
    bridge.add_argument("--subscriber-queue-frames", type=int, default=8)
    bridge.add_argument("--control-idempotency-entries", type=int, default=1024)
    bridge.add_argument(
        "--control-idempotency-retention-ms", type=int, default=300_000
    )
    bridge.add_argument("--control-approval-records", type=int, default=1024)
    bridge.add_argument(
        "--agent-provider",
        choices=("none", "codex"),
        default="none",
        help="enable a text-only Agent provider",
    )
    bridge.add_argument("--agent-workdir", type=Path, default=Path.cwd())
    bridge.add_argument("--agent-max-active-runs", type=int, default=4)
    bridge.add_argument("--agent-session-records", type=int, default=64)
    bridge.add_argument("--agent-run-timeout-seconds", type=float, default=300.0)
    bridge.add_argument("--codex-executable", default="codex")
    bridge.add_argument(
        "--codex-sandbox",
        choices=("read-only", "workspace-write"),
        default="read-only",
    )
    bridge.add_argument(
        "--voice-provider",
        choices=("none", "local"),
        default="none",
        help="enable an explicit provisional local Voice Gateway",
    )
    bridge.add_argument(
        "--voice-asr-provider",
        choices=("paraformer",),
        default="paraformer",
    )
    bridge.add_argument(
        "--voice-tts-provider",
        choices=("piper",),
        default="piper",
    )
    bridge.add_argument("--voice-source", help="stable PipeWire source node name")
    bridge.add_argument("--voice-sink", help="stable PipeWire sink node name")
    bridge.add_argument("--voice-latency", default="20ms")
    bridge.add_argument("--voice-asr-model-directory", type=Path)
    bridge.add_argument("--voice-tts-model", type=Path)
    bridge.add_argument("--voice-tts-config", type=Path)
    bridge.add_argument("--voice-tts-resource-directory", type=Path)
    bridge.add_argument("--voice-cpu-threads", type=int, default=4)
    bridge.add_argument("--voice-max-capture-seconds", type=float, default=30.0)
    bridge.add_argument("--voice-max-capture-bytes", type=int, default=1 << 20)
    bridge.add_argument("--voice-max-speech-items", type=int, default=8)
    bridge.add_argument("--voice-pw-dump-executable", default="pw-dump")
    bridge.add_argument("--voice-wpctl-executable", default="wpctl")
    bridge.add_argument(
        "--voice-pw-cat-command-prefix",
        default="pw-cat",
        metavar="COMMAND",
    )

    audio = subparsers.add_parser(
        "audio",
        help="inspect or explicitly test local audio devices",
    )
    audio_commands = audio.add_subparsers(dest="audio_command", required=True)
    audio_status = audio_commands.add_parser(
        "status",
        help="resolve PipeWire providers and devices without opening audio",
    )
    _add_audio_arguments(audio_status)
    audio_status.add_argument("--list", action="store_true")
    audio_status.add_argument("--json", action="store_true")

    audio_input = audio_commands.add_parser(
        "test-input",
        help="capture a short signal test and discard the PCM",
    )
    _add_audio_arguments(audio_input)
    audio_input.add_argument("--seconds", type=float, default=2.0)
    audio_input.add_argument("--json", action="store_true")

    audio_output = audio_commands.add_parser(
        "test-output",
        help="play a short low-volume test tone",
    )
    _add_audio_arguments(audio_output)
    audio_output.add_argument("--seconds", type=float, default=0.25)
    audio_output.add_argument("--frequency-hz", type=float, default=660.0)
    audio_output.add_argument("--level", type=float, default=0.08)
    audio_output.add_argument("--json", action="store_true")

    emit = subparsers.add_parser("emit", help="send one normalized event")
    add_socket_argument(emit)
    emit.add_argument("--agent-id", required=True)
    emit.add_argument("--slot", type=int, required=True)
    emit.add_argument("--state", choices=[state.value for state in AgentState], required=True)
    emit.add_argument("--label", default="")
    emit.add_argument("--progress", type=float)

    simulate = subparsers.add_parser("simulate", help="cycle four demo agents through states")
    add_socket_argument(simulate)
    simulate.add_argument("--delay", type=float, default=0.35)
    simulate.add_argument("--cycles", type=int, default=1)
    return parser


def _add_audio_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--capture-provider",
        choices=("pipewire",),
        default="pipewire",
    )
    parser.add_argument(
        "--playback-provider",
        choices=("pipewire",),
        default="pipewire",
    )
    parser.add_argument("--source", help="stable PipeWire source node name")
    parser.add_argument("--sink", help="stable PipeWire sink node name")
    parser.add_argument("--latency", default="20ms")
    parser.add_argument("--pw-dump-executable", default="pw-dump")
    parser.add_argument("--wpctl-executable", default="wpctl")
    parser.add_argument(
        "--pw-cat-command-prefix",
        default="pw-cat",
        metavar="COMMAND",
    )


def emit_event(args: argparse.Namespace) -> None:
    event = AgentEvent(
        agent_id=args.agent_id,
        slot=args.slot,
        state=AgentState(args.state),
        label=args.label,
        progress=args.progress,
    )
    send_event(event, args.socket)


def simulate(args: argparse.Namespace) -> None:
    agents = [
        ("frontend", 0),
        ("backend", 1),
        ("tests", 2),
        ("review", 3),
    ]
    states = [
        AgentState.IDLE,
        AgentState.THINKING,
        AgentState.RUNNING_TOOL,
        AgentState.WAITING_APPROVAL,
        AgentState.COMPLETED,
    ]
    for _ in range(args.cycles):
        for state_index, state in enumerate(states):
            for agent_id, slot in agents:
                progress = state_index / (len(states) - 1)
                send_event(
                    AgentEvent(
                        agent_id=f"demo:{agent_id}",
                        slot=slot,
                        state=state,
                        label=agent_id,
                        progress=progress,
                    ),
                    args.socket,
                )
            time.sleep(args.delay)


def audio_command(args: argparse.Namespace) -> None:
    try:
        from deskhelm_voice import (
            AudioProviderKind,
            LocalAudioConfig,
            create_test_tone,
            discover_pipewire_audio,
            test_audio_input,
        )
    except ModuleNotFoundError as error:
        if error.name != "deskhelm_voice":
            raise
        from voice.deskhelm_voice import (
            AudioProviderKind,
            LocalAudioConfig,
            create_test_tone,
            discover_pipewire_audio,
            test_audio_input,
        )

    config = LocalAudioConfig(
        capture_provider=AudioProviderKind(args.capture_provider),
        playback_provider=AudioProviderKind(args.playback_provider),
        source_name=args.source,
        sink_name=args.sink,
        latency=args.latency,
        pw_cat_command_prefix=_parse_command_prefix(
            args.pw_cat_command_prefix,
            "pw-cat",
        ),
    )
    inventory = discover_pipewire_audio(
        pw_dump_executable=args.pw_dump_executable,
        wpctl_executable=args.wpctl_executable,
    )
    selection = config.resolve(inventory)
    if args.audio_command == "status":
        payload = _audio_status_payload(config, inventory, selection, args.list)
        _print_audio_result(payload, args.json)
        return
    if args.audio_command == "test-input":
        provider = config.create_capture_provider(
            max_capture_seconds=12.0,
            max_capture_bytes=1 << 20,
        )
        print(
            "deskhelm: microphone test active; captured PCM will be discarded",
            file=sys.stderr,
        )
        report = test_audio_input(provider, seconds=args.seconds)
        payload = {
            "test": "input",
            "source": selection.source.name,
            "source_description": selection.source.description,
            **asdict(report),
        }
        _print_audio_result(payload, args.json)
        return
    if args.audio_command == "test-output":
        provider = config.create_playback_provider()
        tone = create_test_tone(
            seconds=args.seconds,
            frequency_hz=args.frequency_hz,
            level=args.level,
        )
        print(
            "deskhelm: playing a short low-volume test tone",
            file=sys.stderr,
        )
        provider.play(tone, Event())
        payload = {
            "test": "output",
            "sink": selection.sink.name,
            "sink_description": selection.sink.description,
            "duration_ms": tone.duration_seconds * 1000,
            "frequency_hz": args.frequency_hz,
            "level": args.level,
        }
        _print_audio_result(payload, args.json)
        return
    raise ValueError("unknown audio command")


def _audio_status_payload(
    config,
    inventory,
    selection,
    include_nodes: bool,
) -> dict[str, Any]:
    payload = {
        "capture_provider": config.capture_provider.value,
        "playback_provider": config.playback_provider.value,
        "source": {
            "selection": "default" if selection.source_uses_default else "manual",
            "name": selection.source.name,
            "description": selection.source.description,
        },
        "sink": {
            "selection": "default" if selection.sink_uses_default else "manual",
            "name": selection.sink.name,
            "description": selection.sink.description,
        },
        "available_source_count": len(inventory.sources),
        "available_sink_count": len(inventory.sinks),
    }
    if include_nodes:
        payload["available_sources"] = [asdict(node) for node in inventory.sources]
        payload["available_sinks"] = [asdict(node) for node in inventory.sinks]
    return payload


def _print_audio_result(payload: dict[str, Any], use_json: bool) -> None:
    if use_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if payload.get("test") == "input":
        print(
            "input "
            f"source={payload['source']} "
            f"duration_ms={payload['duration_ms']:.1f} "
            f"peak={payload['peak_fraction']:.4f} "
            f"rms={payload['rms_fraction']:.4f}"
        )
        return
    if payload.get("test") == "output":
        print(
            "output "
            f"sink={payload['sink']} "
            f"duration_ms={payload['duration_ms']:.1f}"
        )
        return
    source = payload["source"]
    sink = payload["sink"]
    print(
        "capture "
        f"provider={payload['capture_provider']} "
        f"selection={source['selection']} "
        f"name={source['name']} "
        f"description={source['description']}"
    )
    print(
        "playback "
        f"provider={payload['playback_provider']} "
        f"selection={sink['selection']} "
        f"name={sink['name']} "
        f"description={sink['description']}"
    )
    if "available_sources" in payload:
        for node in payload["available_sources"]:
            print(f"source name={node['name']} description={node['description']}")
        for node in payload["available_sinks"]:
            print(f"sink name={node['name']} description={node['description']}")


def _parse_command_prefix(value: str, name: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError(f"{name} command prefix is invalid")
    try:
        command = tuple(shlex.split(value))
    except ValueError as error:
        raise ValueError(f"{name} command prefix is invalid") from error
    if not command:
        raise ValueError(f"{name} command prefix is invalid")
    return command


def _compose_local_voice(args: argparse.Namespace, *, inventory=None):
    try:
        from deskhelm_voice import (
            AudioProviderKind,
            LocalAsrProviderKind,
            LocalAudioConfig,
            LocalTtsProviderKind,
            LocalVoiceConfig,
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
            discover_pipewire_audio,
        )

    required = (
        (args.voice_asr_model_directory, "--voice-asr-model-directory"),
        (args.voice_tts_model, "--voice-tts-model"),
        (args.voice_tts_config, "--voice-tts-config"),
        (args.voice_tts_resource_directory, "--voice-tts-resource-directory"),
    )
    missing = [name for value, name in required if value is None]
    if missing:
        raise ValueError("local voice requires " + ", ".join(missing))
    audio = LocalAudioConfig(
        capture_provider=AudioProviderKind.PIPEWIRE,
        playback_provider=AudioProviderKind.PIPEWIRE,
        source_name=args.voice_source,
        sink_name=args.voice_sink,
        latency=args.voice_latency,
        pw_cat_command_prefix=_parse_command_prefix(
            args.voice_pw_cat_command_prefix,
            "pw-cat",
        ),
    )
    config = LocalVoiceConfig(
        audio=audio,
        asr_provider=LocalAsrProviderKind(args.voice_asr_provider),
        asr_model_directory=args.voice_asr_model_directory,
        tts_provider=LocalTtsProviderKind(args.voice_tts_provider),
        tts_model_path=args.voice_tts_model,
        tts_config_path=args.voice_tts_config,
        tts_resource_directory=args.voice_tts_resource_directory,
        cpu_threads=args.voice_cpu_threads,
        max_capture_seconds=args.voice_max_capture_seconds,
        max_capture_bytes=args.voice_max_capture_bytes,
        max_speech_items=args.voice_max_speech_items,
    )
    if inventory is None:
        inventory = discover_pipewire_audio(
            pw_dump_executable=args.voice_pw_dump_executable,
            wpctl_executable=args.voice_wpctl_executable,
        )
    return config.compose(inventory)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "bridge":
            if args.slots < 1:
                raise ValueError("--slots must be at least 1")
            agent_provider = None
            voice_composition = None
            if args.agent_provider == "codex":
                try:
                    from deskhelm_codex_adapter import CodexExecProvider
                except ModuleNotFoundError as error:
                    if error.name != "deskhelm_codex_adapter":
                        raise
                    from adapters.codex.deskhelm_codex_adapter import (
                        CodexExecProvider,
                    )

                agent_provider = CodexExecProvider(
                    command_prefix=(args.codex_executable,),
                    sandbox=args.codex_sandbox,
                    timeout_seconds=args.agent_run_timeout_seconds,
                )
            if args.voice_provider == "local":
                voice_composition = _compose_local_voice(args)
            try:
                return_code = run_bridge(
                    socket_path=args.socket,
                    slot_count=args.slots,
                    stream=sys.stdout,
                    color=not args.no_color,
                    live=not args.plain,
                    max_events=args.max_events,
                    max_connections=args.max_connections,
                    max_subscribers=args.max_subscribers,
                    subscriber_queue_frames=args.subscriber_queue_frames,
                    control_idempotency_entries=args.control_idempotency_entries,
                    control_idempotency_retention_ms=(
                        args.control_idempotency_retention_ms
                    ),
                    control_approval_records=args.control_approval_records,
                    agent_provider=agent_provider,
                    agent_working_directory=args.agent_workdir,
                    agent_max_active_runs=args.agent_max_active_runs,
                    agent_session_records=args.agent_session_records,
                    voice_gateway=(
                        voice_composition.gateway
                        if voice_composition is not None
                        else None
                    ),
                )
            finally:
                if voice_composition is not None:
                    voice_composition.gateway.close()
            return 0 if return_code >= 0 else 1
        if args.command == "emit":
            emit_event(args)
            return 0
        if args.command == "simulate":
            simulate(args)
            return 0
        if args.command == "audio":
            audio_command(args)
            return 0
    except (ConnectionError, ProtocolError, RuntimeError, ValueError) as error:
        print(f"deskhelm: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
