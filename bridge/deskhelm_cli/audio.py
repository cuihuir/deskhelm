"""``deskhelm audio``: inspect or explicitly test the local audio devices.

``status`` resolves PipeWire devices without opening audio. ``test-input``
captures briefly and discards the PCM after measuring its level;
``test-output`` plays a short low-volume tone. A configured device that is
missing fails instead of falling back.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import shlex
import sys
from threading import Event
from typing import Any


def _voice() -> Any:
    try:
        import deskhelm_voice
    except ModuleNotFoundError as error:
        if error.name != "deskhelm_voice":
            raise
        from voice import deskhelm_voice
    return deskhelm_voice


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deskhelm audio", description="Inspect or test the local audio devices.")
    commands = parser.add_subparsers(dest="audio_command", required=True)
    status = commands.add_parser("status", help="resolve PipeWire devices without opening audio")
    _add_device_arguments(status)
    status.add_argument("--list", action="store_true", help="also list every source and sink")
    status.add_argument("--json", action="store_true")
    test_input = commands.add_parser("test-input", help="capture briefly, report the level, discard the audio")
    _add_device_arguments(test_input)
    test_input.add_argument("--seconds", type=float, default=2.0)
    test_input.add_argument("--json", action="store_true")
    test_output = commands.add_parser("test-output", help="play a short low-volume tone")
    _add_device_arguments(test_output)
    test_output.add_argument("--seconds", type=float, default=0.25)
    test_output.add_argument("--frequency-hz", type=float, default=660.0)
    test_output.add_argument("--level", type=float, default=0.08)
    test_output.add_argument("--json", action="store_true")
    return parser


def _add_device_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", help="stable PipeWire source node name")
    parser.add_argument("--sink", help="stable PipeWire sink node name")
    parser.add_argument("--latency", default="20ms")
    parser.add_argument("--pw-dump-executable", default="pw-dump")
    parser.add_argument("--wpctl-executable", default="wpctl")
    parser.add_argument("--pw-cat-command-prefix", default="pw-cat", metavar="COMMAND")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"deskhelm audio: {error}", file=sys.stderr)
        return 1
    return 0


def run(args: argparse.Namespace) -> None:
    voice = _voice()
    try:
        prefix = tuple(shlex.split(args.pw_cat_command_prefix))
    except ValueError as error:
        raise ValueError("the pw-cat command prefix is invalid") from error
    config = voice.LocalAudioConfig(
        source_name=args.source,
        sink_name=args.sink,
        latency=args.latency,
        pw_cat_command_prefix=prefix,
    )
    inventory = voice.discover_pipewire_audio(
        pw_dump_executable=args.pw_dump_executable, wpctl_executable=args.wpctl_executable
    )
    selection = config.resolve(inventory)
    if args.audio_command == "status":
        _print(_status(inventory, selection, args.list), args.json)
    elif args.audio_command == "test-input":
        provider = config.create_capture_provider(max_capture_seconds=12.0, max_capture_bytes=1 << 20)
        print("deskhelm: microphone test active; captured PCM will be discarded", file=sys.stderr)
        report = voice.test_audio_input(provider, seconds=args.seconds)
        _print(
            {"test": "input", "source": selection.source.name, "source_description": selection.source.description,
             **asdict(report)},
            args.json,
        )
    else:
        tone = voice.create_test_tone(seconds=args.seconds, frequency_hz=args.frequency_hz, level=args.level)
        print("deskhelm: playing a short low-volume test tone", file=sys.stderr)
        config.create_playback_provider().play(tone, Event())
        _print(
            {"test": "output", "sink": selection.sink.name, "sink_description": selection.sink.description,
             "duration_ms": tone.duration_seconds * 1000, "frequency_hz": args.frequency_hz, "level": args.level},
            args.json,
        )


def _status(inventory: Any, selection: Any, include_nodes: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "capture_provider": "pipewire",
        "playback_provider": "pipewire",
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


def _print(payload: dict[str, Any], use_json: bool) -> None:
    if use_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if payload.get("test") == "input":
        print(
            f"input source={payload['source']} duration_ms={payload['duration_ms']:.1f} "
            f"peak={payload['peak_fraction']:.4f} rms={payload['rms_fraction']:.4f}"
        )
        return
    if payload.get("test") == "output":
        print(f"output sink={payload['sink']} duration_ms={payload['duration_ms']:.1f}")
        return
    for kind, side in (("capture", "source"), ("playback", "sink")):
        node = payload[side]
        print(
            f"{kind} provider={payload[kind + '_provider']} selection={node['selection']} "
            f"name={node['name']} description={node['description']}"
        )
    for node in payload.get("available_sources", []):
        print(f"source name={node['name']} description={node['description']}")
    for node in payload.get("available_sinks", []):
        print(f"sink name={node['name']} description={node['description']}")
