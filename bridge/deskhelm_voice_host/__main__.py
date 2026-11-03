"""Run the voice host: ``python -m deskhelm_voice_host [--socket PATH]``.

Run it from the voice runtime, which holds the model providers::

    PYTHONPATH=<checkout>/bridge:<checkout>/voice \\
      uv run --project <checkout>/voice/runtime --locked --no-sync \\
      python -m deskhelm_voice_host

It attaches to a running v2 Bridge at the voice tier. Holding right Alt
speaks to the session the Bridge has in focus. Model and audio settings
default to the ``DESKHELM_ALPHA_*`` variables that ``deskhelm codex --voice``
reads, so ``project-secrets run`` can supply them without storing them.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
from threading import Event, Thread
from typing import Any

from deskhelm_client import BridgeClient
from deskhelm_protocol import Tier
from deskhelm_protocol.paths import default_socket_path

from .keys import DEFAULT_DEVICE

ROOT = Path(__file__).resolve().parents[2]
ASR_ENV = "DESKHELM_ALPHA_ASR_MODEL_DIRECTORY"
TTS_ENV = "DESKHELM_ALPHA_TTS_DIRECTORY"
SOURCE_ENV = "DESKHELM_ALPHA_VOICE_SOURCE"
SINK_ENV = "DESKHELM_ALPHA_VOICE_SINK"
PIPER_MODEL_NAME = "zh_CN-chaowen-medium.onnx"
PIPER_CONFIG_NAME = "zh_CN-chaowen-medium.onnx.json"
KOKORO_MODEL_NAME = "kokoro-v1_0.pth"
KOKORO_CONFIG_NAME = "config.json"
INDEXTTS_CONFIG_NAME = "config.yaml"
INDEXTTS_VOICE_NAME = "voice.wav"
INDEXTTS_EXECUTABLE = ROOT / "tools" / "run-index-tts25"
STOP_SECONDS = 2.0


def _voice() -> Any:
    try:
        import deskhelm_voice
    except ModuleNotFoundError as error:
        if error.name != "deskhelm_voice":
            raise
        from voice import deskhelm_voice
    return deskhelm_voice


def _is_indextts(directory: Path) -> bool:
    return (directory / INDEXTTS_CONFIG_NAME).is_file() and (directory / INDEXTTS_VOICE_NAME).is_file()


def tts_settings(directory: Path) -> dict[str, Any]:
    """Find the TTS provider in a directory, as ``deskhelm codex --voice`` does."""
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise ValueError("the TTS directory is unavailable")
    if not _is_indextts(directory):
        # A downloaded IndexTTS model may sit in a child of a Piper or
        # Kokoro directory; it wins, as it does for the v1 launcher.
        child = next((item for item in sorted(directory.iterdir()) if item.is_dir() and _is_indextts(item)), None)
        if child is not None:
            directory = child
    if _is_indextts(directory):
        return {"provider": "indextts", "model": directory, "config": directory / INDEXTTS_CONFIG_NAME,
                "resources": directory, "voice": directory / INDEXTTS_VOICE_NAME, "executable": str(INDEXTTS_EXECUTABLE)}
    if (directory / PIPER_MODEL_NAME).is_file():
        return {"provider": "piper", "model": directory / PIPER_MODEL_NAME, "config": directory / PIPER_CONFIG_NAME,
                "resources": directory, "voice": None, "executable": "indextts2"}
    if (directory / KOKORO_MODEL_NAME).is_file():
        return {"provider": "kokoro", "model": directory / KOKORO_MODEL_NAME, "config": directory / KOKORO_CONFIG_NAME,
                "resources": directory, "voice": None, "executable": "indextts2"}
    raise ValueError("the TTS directory holds no supported voice")


def build_config(args: argparse.Namespace) -> Any:
    voice = _voice()
    tts = tts_settings(args.tts_directory)
    try:
        prefix = tuple(shlex.split(args.pw_cat_command_prefix))
    except ValueError as error:
        raise ValueError("the pw-cat command prefix is invalid") from error
    audio = voice.LocalAudioConfig(
        source_name=args.source or None,
        sink_name=args.sink or None,
        latency=args.latency,
        pw_cat_command_prefix=prefix,
    )
    return voice.LocalVoiceConfig(
        audio=audio,
        asr_provider=voice.LocalAsrProviderKind.PARAFORMER,
        asr_model_directory=args.asr_model_directory.expanduser().resolve(),
        tts_provider=voice.LocalTtsProviderKind(tts["provider"]),
        tts_model_path=tts["model"],
        tts_config_path=tts["config"],
        tts_resource_directory=tts["resources"],
        tts_voice_path=tts["voice"],
        tts_executable=tts["executable"],
        cpu_threads=args.cpu_threads,
    )


def _parser() -> argparse.ArgumentParser:
    environment = os.environ
    parser = argparse.ArgumentParser(prog="deskhelm-voice", description="Attach local voice to the v2 Bridge.")
    parser.add_argument("--socket", type=Path, default=None)
    parser.add_argument("--asr-model-directory", type=Path, default=_path(environment.get(ASR_ENV)))
    parser.add_argument("--tts-directory", type=Path, default=_path(environment.get(TTS_ENV)))
    parser.add_argument("--source", default=None,
                        help=f"stable PipeWire source name, else {SOURCE_ENV}; a missing one fails without fallback")
    parser.add_argument("--sink", default=None, help=f"stable PipeWire sink name, else {SINK_ENV}")
    parser.add_argument("--default-source", action="store_true",
                        help="use the computer's current default microphone even when a source is configured")
    parser.add_argument("--default-sink", action="store_true",
                        help="use the computer's current default speaker even when a sink is configured")
    parser.add_argument("--latency", default="20ms")
    parser.add_argument("--pw-cat-command-prefix", default="pw-cat")
    parser.add_argument("--pw-dump-executable", default="pw-dump")
    parser.add_argument("--wpctl-executable", default="wpctl")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--ptt-device", type=Path, default=DEFAULT_DEVICE)
    parser.add_argument("--ptt-python", type=Path, default=ROOT / ".venv" / "bin" / "python",
                        help="a Python that has evdev, from `uv sync --extra ptt`")
    parser.add_argument("--no-ptt", action="store_true", help="speak notices only")
    parser.add_argument("--check", action="store_true", help="check models, audio, and the PTT key, then exit")
    return parser


def _path(value: str | None) -> Path | None:
    return Path(value) if value and value.strip() else None


def _device(parser: argparse.ArgumentParser, explicit: str | None, use_default: bool, variable: str, flag: str) -> str | None:
    if use_default and explicit:
        parser.error(f"--{flag} and --default-{flag} conflict")
    if use_default:
        return None
    return explicit or os.environ.get(variable, "").strip() or None


def missing_devices(inventory: Any, source: str | None, sink: str | None) -> list[str]:
    """Configured devices that PipeWire does not list right now."""
    missing = []
    if source and all(node.name != source for node in inventory.sources):
        missing.append("source")
    if sink and all(node.name != sink for node in inventory.sinks):
        missing.append("sink")
    return missing


_MISSING_DEVICE = {
    # Device names can come from the secret profile, so they are not shown.
    "source": f"the configured microphone ({SOURCE_ENV} or --source) is not connected; connect it, "
    "or add --default-source to use the computer's current default microphone",
    "sink": f"the configured speaker ({SINK_ENV} or --sink) is not connected; connect it, "
    "or add --default-sink to use the computer's current default speaker",
}


def _keys_command(args: argparse.Namespace, *extra: str) -> list[str]:
    return [str(args.ptt_python), "-m", "deskhelm_voice_host.keys", "--device", str(args.ptt_device), *extra]


def _keys_environment() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(ROOT / "bridge")}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    args.source = _device(parser, args.source, args.default_source, SOURCE_ENV, "source")
    args.sink = _device(parser, args.sink, args.default_sink, SINK_ENV, "sink")
    missing = [name for name, value in (("--asr-model-directory", args.asr_model_directory),
                                         ("--tts-directory", args.tts_directory)) if value is None]
    if missing:
        parser.error("local voice needs " + ", ".join(missing) + f" (or {ASR_ENV} and {TTS_ENV})")
    if not args.no_ptt and not args.ptt_python.is_file():
        parser.error(f"{args.ptt_python} is missing; run `uv sync --extra ptt --locked` in {ROOT} or pass --no-ptt")
    try:
        config = build_config(args)
        inventory = _voice().discover_pipewire_audio(
            pw_dump_executable=args.pw_dump_executable, wpctl_executable=args.wpctl_executable
        )
        absent = missing_devices(inventory, args.source, args.sink)
        if absent:
            for kind in absent:
                print(f"deskhelm voice: {_MISSING_DEVICE[kind]}", file=sys.stderr)
            return 1
        composition = config.compose(inventory)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"deskhelm voice: {error}", file=sys.stderr)
        return 1
    gateway = composition.gateway
    if args.check:
        gateway.close()
        selection = composition.audio_selection
        # Device names can come from the secret profile, so only say which
        # side follows the computer default.
        print(
            "deskhelm voice: source "
            + ("default" if selection.source_uses_default else "selected")
            + ", sink "
            + ("default" if selection.sink_uses_default else "selected"),
            file=sys.stderr,
        )
        if not args.no_ptt:
            checked = subprocess.run(_keys_command(args, "--check"), env=_keys_environment(),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, check=False)
            if checked.returncode != 0:
                return 1
        print("deskhelm voice: preflight passed", file=sys.stderr)
        return 0
    return _run(args, gateway)


def _run(args: argparse.Namespace, gateway: Any) -> int:
    from .policy import VoiceHost

    stop = Event()
    holder: dict[str, VoiceHost] = {}
    socket_path = args.socket or default_socket_path()
    client = BridgeClient(
        socket_path,
        client_id="deskhelm-voice",
        tier=Tier.VOICE,
        on_console=lambda change: holder["host"].on_console(change),
    )
    host = holder["host"] = VoiceHost(client, gateway)
    keys: subprocess.Popen[str] | None = None
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    for number in previous:
        signal.signal(number, lambda _number, _frame: stop.set())
    code = 0
    try:
        host.start()
        client.start()
        if not args.no_ptt:
            keys = subprocess.Popen(_keys_command(args), env=_keys_environment(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, text=True, bufsize=1)
            Thread(target=_read_keys, args=(keys, host, stop), name="deskhelm-voice-keys", daemon=True).start()
        how = "hold right Alt to speak to the focused session" if keys else "notices only"
        print(f"deskhelm voice: attached to {socket_path}; {how}", file=sys.stderr)
        while not stop.wait(0.5):
            pass
        if keys is not None and keys.poll() not in (None, 0):
            code = 1
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        if keys is not None and keys.poll() is None:
            keys.terminate()
            try:
                keys.wait(timeout=STOP_SECONDS)
            except subprocess.TimeoutExpired:
                keys.kill()
                keys.wait()
        client.close()
        host.close()
        gateway.close()
    return code


def _read_keys(keys: subprocess.Popen[str], host: Any, stop: Event) -> None:
    assert keys.stdout is not None
    for line in keys.stdout:
        edge = line.strip()
        if edge == "down":
            host.press()
        elif edge == "up":
            host.release()
    # Without its key the host cannot take speech; stop rather than linger.
    stop.set()


if __name__ == "__main__":
    raise SystemExit(main())
