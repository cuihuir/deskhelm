from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
from importlib import metadata
import importlib.util
import os
from pathlib import Path
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import termios
import time
from typing import Mapping

from .adapter import AdapterCapability, AdapterSessionAction, AdapterSessionEvent
from .client import (
    AdapterSessionPublisher,
    attach_agent_runtime,
    detach_agent_runtime,
    query_agent_runtime_directory,
    send_control_command,
)
from .control import ControlCommand, ControlKind, FocusPayload
from .control_result import ControlResultStatus
from .event import ProtocolError
from .paths import (
    alpha_codex_socket_paths,
    alpha_directory_digest,
    default_alpha_socket_path,
    default_socket_path,
)
from .transport import INTERACTION_EVENT_V1_CAPABILITY


ASR_MODEL_DIRECTORY_ENV = "DESKHELM_ALPHA_ASR_MODEL_DIRECTORY"
TTS_DIRECTORY_ENV = "DESKHELM_ALPHA_TTS_DIRECTORY"
VOICE_SOURCE_ENV = "DESKHELM_ALPHA_VOICE_SOURCE"
VOICE_SINK_ENV = "DESKHELM_ALPHA_VOICE_SINK"
PIPER_MODEL_NAME = "zh_CN-chaowen-medium.onnx"
PIPER_CONFIG_NAME = "zh_CN-chaowen-medium.onnx.json"
KOKORO_MODEL_NAME = "kokoro-v1_0.pth"
KOKORO_CONFIG_NAME = "config.json"
KOKORO_CHINESE_VOICE_NAME = "zf_xiaobei.pt"
KOKORO_ENGLISH_VOICE_NAME = "af_heart.pt"
INDEXTTS_CONFIG_NAME = "config.yaml"
INDEXTTS_VOICE_NAME = "voice.wav"
INDEXTTS_EXECUTABLE = str(
    Path(__file__).resolve().parents[2] / "tools" / "run-index-tts25"
)
SESSION_START_TIMEOUT_SECONDS = 15.0
APP_SERVER_START_TIMEOUT_SECONDS = 15.0
PROCESS_STOP_TIMEOUT_SECONDS = 5.0
SHARED_BRIDGE_SLOTS = 32
SHARED_BRIDGE_MAX_CONNECTIONS = 64
PRIVATE_SESSION_ID = "local-alpha"
PRIVATE_CLIENT_ID = "deskhelm-local-alpha"


@dataclass(frozen=True, slots=True)
class LocalAlphaArtifacts:
    asr_model_directory: Path
    tts_directory: Path
    tts_provider: str = "piper"
    tts_executable: str = "indextts2"

    @property
    def tts_model_path(self) -> Path:
        if self.tts_provider == "indextts":
            return self.tts_directory
        name = (
            PIPER_MODEL_NAME
            if self.tts_provider == "piper"
            else KOKORO_MODEL_NAME
        )
        return self.tts_directory / name

    @property
    def tts_config_path(self) -> Path:
        if self.tts_provider == "indextts":
            return self.tts_directory / INDEXTTS_CONFIG_NAME
        name = (
            PIPER_CONFIG_NAME
            if self.tts_provider == "piper"
            else KOKORO_CONFIG_NAME
        )
        return self.tts_directory / name

    @property
    def tts_voice_path(self) -> Path:
        return self.tts_directory / INDEXTTS_VOICE_NAME

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
    ) -> LocalAlphaArtifacts:
        missing = [
            name
            for name in (ASR_MODEL_DIRECTORY_ENV, TTS_DIRECTORY_ENV)
            if not environment.get(name, "").strip()
        ]
        if missing:
            raise ValueError(
                "local alpha requires environment variables " + ", ".join(missing)
            )
        tts_directory = _absolute_path(environment[TTS_DIRECTORY_ENV])
        tts_provider = "piper"
        if tts_directory.is_dir():
            tts_provider = _detect_tts_provider(tts_directory)
            if tts_provider != "indextts":
                # Keep existing Piper/Kokoro assets intact while allowing a
                # downloaded IndexTTS model in a dedicated child directory.
                for child in sorted(tts_directory.iterdir()):
                    if child.is_dir() and _has_indextts_markers(child):
                        tts_directory = child
                        tts_provider = "indextts"
                        break
        return cls(
            asr_model_directory=_absolute_path(environment[ASR_MODEL_DIRECTORY_ENV]),
            tts_directory=tts_directory,
            tts_provider=tts_provider,
            tts_executable=(
                INDEXTTS_EXECUTABLE if tts_provider == "indextts" else "indextts2"
            ),
        )

    def validate(self) -> None:
        if self.tts_provider not in {"piper", "kokoro", "indextts"}:
            raise ValueError("local alpha TTS provider is unsupported")
        _require_directory(self.asr_model_directory, "Paraformer model directory")
        for name in ("model.pt", "config.yaml", "tokens.json", "am.mvn", "seg_dict"):
            _require_file(self.asr_model_directory / name, f"Paraformer {name}")
        _require_directory(self.tts_directory, "TTS directory")
        if self.tts_provider == "indextts":
            _require_directory(self.tts_model_path, "IndexTTS model directory")
            _require_file(self.tts_config_path, "IndexTTS config")
            _require_file(self.tts_voice_path, "IndexTTS speaker reference")
            return
        if self.tts_provider == "piper":
            required = (
                (self.tts_model_path, "Piper model"),
                (self.tts_config_path, "Piper config"),
                (
                    self.tts_directory / "g2pW" / "g2pw.onnx",
                    "Piper g2pW model",
                ),
                (self.tts_directory / "tokenizer.json", "Piper tokenizer"),
                (
                    self.tts_directory / "tokenizer_config.json",
                    "Piper tokenizer config",
                ),
                (self.tts_directory / "vocab.txt", "Piper vocabulary"),
            )
        else:
            required = (
                (self.tts_model_path, "Kokoro model"),
                (self.tts_config_path, "Kokoro config"),
                (
                    self.tts_directory / KOKORO_CHINESE_VOICE_NAME,
                    "Kokoro Chinese voice",
                ),
                (
                    self.tts_directory / KOKORO_ENGLISH_VOICE_NAME,
                    "Kokoro English voice",
                ),
            )
        for path, label in required:
            _require_file(path, label)


@dataclass(frozen=True, slots=True)
class LocalAlphaProfile:
    repository_root: Path
    runtime_project: Path
    agent_workdir: Path
    socket_path: Path
    shared_bridge: bool
    codex_app_server_socket: Path
    codex_app_server_upstream_socket: Path
    input_device: Path
    agent_id: str
    session_id: str
    project_id: str
    client_id: str
    uv_executable: str
    codex_executable: str
    codex_sandbox: str
    yolo_enabled: bool
    launch_tui: bool
    voice_enabled: bool
    source_name: str | None
    sink_name: str | None
    pw_cat_command_prefix: str
    voice_turn_timing: bool
    artifacts: LocalAlphaArtifacts | None

    @classmethod
    def from_args(
        cls,
        args: argparse.Namespace,
        environment: Mapping[str, str],
    ) -> LocalAlphaProfile:
        repository_root = Path(__file__).resolve().parents[2]
        agent_workdir = _absolute_path(args.agent_workdir)
        runtime_project = _absolute_path(
            args.runtime_project or repository_root / "voice" / "runtime"
        )
        project_id = args.project_id or agent_workdir.name
        shared_bridge = args.socket is None
        if shared_bridge:
            socket_path = default_socket_path()
            codex_stem = default_alpha_socket_path(agent_workdir)
        else:
            socket_path = _absolute_path(args.socket)
            codex_stem = socket_path
        codex_proxy, _codex_upstream = alpha_codex_socket_paths(codex_stem)
        codex_app_server_socket = _absolute_path(
            args.codex_app_server_socket or codex_proxy
        )
        codex_app_server_upstream_socket = codex_app_server_socket.with_name(
            f"{codex_app_server_socket.stem}-upstream"
            f"{codex_app_server_socket.suffix}"
        )
        digest = alpha_directory_digest(agent_workdir)
        session_id = args.session_id
        client_id = args.client_id
        if shared_bridge and session_id == PRIVATE_SESSION_ID:
            session_id = f"local-{digest}"
        if shared_bridge and client_id == PRIVATE_CLIENT_ID:
            client_id = f"{PRIVATE_CLIENT_ID}-{digest}"
        identifiers = {
            "agent": args.agent_id,
            "session": session_id,
            "project": project_id,
            "client": client_id,
        }
        invalid = [name for name, value in identifiers.items() if not value.strip()]
        if invalid:
            raise ValueError(
                "local alpha identifiers are empty: " + ", ".join(invalid)
            )
        if not args.voice and any(
            value is not None
            for value in (args.voice_source, args.voice_sink)
        ):
            raise ValueError("voice device selection requires --voice")
        if args.voice_turn_timing and not args.voice:
            raise ValueError("--voice-turn-timing requires --voice")
        return cls(
            repository_root=repository_root,
            runtime_project=runtime_project,
            agent_workdir=agent_workdir,
            socket_path=socket_path,
            shared_bridge=shared_bridge,
            codex_app_server_socket=codex_app_server_socket,
            codex_app_server_upstream_socket=(
                codex_app_server_upstream_socket
            ),
            input_device=Path(args.input_device).expanduser(),
            agent_id=args.agent_id,
            session_id=session_id,
            project_id=project_id,
            client_id=client_id,
            uv_executable=args.uv_executable,
            codex_executable=args.codex_executable,
            codex_sandbox=args.codex_sandbox,
            yolo_enabled=args.yolo,
            launch_tui=args.tui,
            voice_enabled=args.voice,
            source_name=(
                args.voice_source
                if args.voice_source is not None
                else environment.get(VOICE_SOURCE_ENV, "").strip() or None
            ),
            sink_name=(
                args.voice_sink
                if args.voice_sink is not None
                else environment.get(VOICE_SINK_ENV, "").strip() or None
            ),
            pw_cat_command_prefix=args.voice_pw_cat_command_prefix,
            voice_turn_timing=args.voice_turn_timing,
            artifacts=(
                LocalAlphaArtifacts.from_environment(environment)
                if args.voice
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class LocalAlphaRuntime:
    uv_executable: str | None
    codex_executable: str
    codex_version: str
    environment: dict[str, str]


@dataclass(frozen=True, slots=True)
class _TerminalState:
    file_descriptor: int
    foreground_process_group: int
    attributes: list[object]


class _AlphaTermination(Exception):
    def __init__(self, signal_number: int) -> None:
        super().__init__(signal_number)
        self.signal_number = signal_number


def run_local_alpha(
    args: argparse.Namespace,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    source_environment = os.environ if environment is None else environment
    profile = LocalAlphaProfile.from_args(args, source_environment)
    if profile.launch_tui and not args.check and not _has_interactive_terminal():
        raise ValueError("deskhelm codex --tui requires an interactive terminal")
    runtime = _preflight(profile, source_environment)
    if args.check:
        print("deskhelm: local alpha profile preflight passed", file=sys.stderr)
        return 0
    terminal_state = _capture_terminal_state() if profile.launch_tui else None

    bridge = None
    app_server = None
    ptt = None
    tui = None
    publisher = None
    attached = False
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    stopping = False
    exit_code = 0
    failure: BaseException | None = None
    cleanup_errors: list[BaseException] = []

    def terminate(signal_number: int, _frame: object) -> None:
        if stopping:
            return
        raise _AlphaTermination(signal_number)

    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGTERM, terminate)
    try:
        app_server = subprocess.Popen(
            _app_server_command(profile, runtime),
            cwd=profile.agent_workdir,
            env=runtime.environment,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_for_owned_socket(
            profile.codex_app_server_upstream_socket,
            app_server,
            "Codex app-server",
        )
        if profile.shared_bridge:
            bridge = _ensure_user_bridge(profile, runtime)
            try:
                _attach_runtime(profile, runtime.codex_version)
            except (ConnectionError, OSError, ProtocolError):
                attached = True
                raise
            attached = True
        else:
            bridge = subprocess.Popen(
                _bridge_command(profile, runtime),
                cwd=profile.repository_root,
                env=runtime.environment,
                start_new_session=True,
            )
        publisher = _register_session(profile, runtime.codex_version, bridge)
        if profile.voice_enabled:
            _focus_session(profile)
            ptt = subprocess.Popen(
                _ptt_command(profile),
                cwd=profile.repository_root,
                env=runtime.environment,
                start_new_session=True,
            )
        if profile.launch_tui:
            _wait_for_owned_socket(
                profile.codex_app_server_socket,
                bridge,
                "Bridge TUI proxy",
            )
            print(
                f"deskhelm: local alpha active for {profile.project_id}; "
                "launching managed Codex TUI",
                file=sys.stderr,
            )
            tui = subprocess.Popen(
                _remote_tui_command(profile, runtime),
                cwd=profile.agent_workdir,
                env=runtime.environment,
                process_group=0,
            )
            _set_terminal_foreground(
                terminal_state.file_descriptor,
                tui.pid,
            )
        else:
            print(
                f"deskhelm: local alpha active for {profile.project_id}; "
                "start the manual Codex TUI with:\n"
                f"  {shlex.join(_remote_tui_command(profile, runtime))}\n"
                "press Ctrl-C here to stop DeskHelm",
                file=sys.stderr,
            )
        while True:
            if app_server.poll() is not None:
                raise RuntimeError("local alpha Codex app-server exited unexpectedly")
            if bridge is not None and bridge.poll() is not None:
                raise RuntimeError("local alpha Bridge exited unexpectedly")
            if (
                profile.shared_bridge
                and bridge is None
                and not profile.socket_path.exists()
            ):
                raise RuntimeError("user bridge is no longer available")
            if ptt is not None and ptt.poll() is not None:
                raise RuntimeError("local alpha PTT controller exited unexpectedly")
            if tui is not None:
                return_code = tui.poll()
                if return_code is not None:
                    exit_code = _normalized_return_code(return_code)
                    break
            time.sleep(0.1)
    except KeyboardInterrupt:
        exit_code = 130
    except _AlphaTermination as termination:
        exit_code = 128 + termination.signal_number
    except BaseException as error:
        failure = error
    finally:
        stopping = True

        def cleanup(action) -> None:
            try:
                action()
            except BaseException as error:
                cleanup_errors.append(error)

        if tui is not None:
            cleanup(lambda: _stop_tui_process(tui))
        if terminal_state is not None:
            cleanup(lambda: _restore_terminal(terminal_state))
        if ptt is not None:
            cleanup(lambda: _stop_process(ptt))
        if publisher is not None:
            cleanup(
                lambda: _release_session(
                    profile,
                    runtime.codex_version,
                    publisher,
                )
            )
            cleanup(publisher.close)
        if attached:
            cleanup(lambda: _detach_runtime(profile))
        if bridge is not None and not profile.shared_bridge:
            cleanup(lambda: _stop_process(bridge))
        if app_server is not None:
            cleanup(lambda: _stop_process(app_server))
        if not profile.shared_bridge:
            cleanup(lambda: profile.socket_path.unlink(missing_ok=True))
        cleanup(lambda: profile.codex_app_server_socket.unlink(missing_ok=True))
        cleanup(
            lambda: profile.codex_app_server_upstream_socket.unlink(
                missing_ok=True
            )
        )
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    if failure is not None:
        raise failure
    if cleanup_errors:
        raise RuntimeError("local alpha cleanup failed") from cleanup_errors[0]
    print("deskhelm: local alpha stopped", file=sys.stderr)
    return exit_code


def _preflight(
    profile: LocalAlphaProfile,
    environment: Mapping[str, str],
) -> LocalAlphaRuntime:
    _require_directory(profile.agent_workdir, "Agent working directory")
    socket_paths = {
        profile.socket_path,
        profile.codex_app_server_socket,
        profile.codex_app_server_upstream_socket,
    }
    if len(socket_paths) != 3:
        raise ValueError("Bridge, TUI proxy, and app-server sockets must differ")
    checked_sockets = [
        (profile.codex_app_server_socket, "local alpha TUI proxy socket"),
        (
            profile.codex_app_server_upstream_socket,
            "local alpha Codex app-server socket",
        ),
    ]
    if not profile.shared_bridge:
        checked_sockets.insert(0, (profile.socket_path, "local alpha socket"))
    for path, label in checked_sockets:
        if path.exists():
            raise ValueError(f"{label} already exists")
    codex_executable = shutil.which(profile.codex_executable)
    if codex_executable is None:
        raise RuntimeError("local alpha requires the Codex executable")

    child_environment = _child_environment(profile, environment)
    uv_executable = None
    if profile.voice_enabled:
        artifacts = _voice_artifacts(profile)
        artifacts.validate()
        _require_file(
            profile.runtime_project / "pyproject.toml",
            "Voice runtime project",
        )
        if not profile.input_device.is_char_device() or not os.access(
            profile.input_device,
            os.R_OK,
        ):
            raise ValueError("right-Alt PTT input device is unavailable")
        if importlib.util.find_spec("evdev") is None:
            raise RuntimeError(
                "right-Alt PTT requires `uv sync --extra ptt --locked`"
            )
        uv_executable = shutil.which(profile.uv_executable)
        if uv_executable is None:
            raise RuntimeError("local voice requires the uv executable")
        try:
            dependency_check = subprocess.run(
                [
                    uv_executable,
                    "run",
                    "--project",
                    str(profile.runtime_project),
                    "--locked",
                    "--no-sync",
                    "python",
                    "-c",
                    (
                        "import funasr, numpy, onnxruntime, torch, "
                        + (
                            "shutil; assert shutil.which(%r)"
                            % artifacts.tts_executable
                            if artifacts.tts_provider == "indextts"
                            else artifacts.tts_provider
                        )
                    ),
                ],
                cwd=profile.repository_root,
                env=child_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("local voice runtime preflight timed out") from error
        if dependency_check.returncode != 0:
            raise RuntimeError(
                "local voice runtime is unavailable; run "
                "`cd voice/runtime && uv sync --locked --python 3.12`"
            )

        try:
            audio_check = subprocess.run(
                _audio_check_command(profile, uv_executable),
                cwd=profile.repository_root,
                env=child_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("local alpha PipeWire preflight timed out") from error
        if audio_check.returncode != 0:
            raise RuntimeError("local alpha PipeWire preflight failed")

    try:
        version_check = subprocess.run(
            [codex_executable, "--version"],
            cwd=profile.agent_workdir,
            env=child_environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("local alpha Codex version check timed out") from error
    codex_version = version_check.stdout.strip().splitlines()
    if version_check.returncode != 0 or not codex_version:
        raise RuntimeError("local alpha could not determine the Codex version")
    return LocalAlphaRuntime(
        uv_executable=uv_executable,
        codex_executable=codex_executable,
        codex_version=codex_version[0][:128],
        environment=child_environment,
    )


def _bridge_command(
    profile: LocalAlphaProfile,
    runtime: LocalAlphaRuntime,
) -> list[str]:
    if profile.voice_enabled:
        if runtime.uv_executable is None:
            raise RuntimeError("local voice runtime is unavailable")
        prefix = [
            runtime.uv_executable,
            "run",
            "--project",
            str(profile.runtime_project),
            "--locked",
            "--no-sync",
            "python",
        ]
    else:
        prefix = [sys.executable]
    if profile.shared_bridge:
        command = [
            *prefix,
            "-m",
            "deskhelm_bridge",
            "bridge",
            "--plain",
            "--socket",
            str(profile.socket_path),
            "--slots",
            str(SHARED_BRIDGE_SLOTS),
            "--max-connections",
            str(SHARED_BRIDGE_MAX_CONNECTIONS),
            "--agent-runtime-directory",
        ]
    else:
        command = [
            *prefix,
            "-m",
            "deskhelm_bridge",
            "bridge",
            "--plain",
            "--socket",
            str(profile.socket_path),
            "--agent-provider",
            "codex-app-server",
            "--agent-id",
            profile.agent_id,
            "--agent-session-id",
            profile.session_id,
            "--agent-project-id",
            profile.project_id,
            "--codex-app-server-socket",
            str(profile.codex_app_server_upstream_socket),
            "--codex-tui-proxy-socket",
            str(profile.codex_app_server_socket),
            "--codex-executable",
            runtime.codex_executable,
            "--codex-runtime-version",
            runtime.codex_version,
        ]
    if profile.voice_enabled:
        artifacts = _voice_artifacts(profile)
        command.extend(
            (
                "--voice-provider",
                "local",
                "--voice-asr-provider",
                "paraformer",
                "--voice-asr-model-directory",
                str(artifacts.asr_model_directory),
                "--voice-tts-provider",
                artifacts.tts_provider,
                "--voice-tts-model",
                str(artifacts.tts_model_path),
                "--voice-tts-config",
                str(artifacts.tts_config_path),
                "--voice-tts-resource-directory",
                str(artifacts.tts_directory),
                "--voice-tts-executable",
                artifacts.tts_executable,
                "--voice-vad-provider",
                "none",
                "--voice-pw-cat-command-prefix",
                profile.pw_cat_command_prefix,
            )
        )
        if artifacts.tts_provider == "indextts":
            command.extend(("--voice-tts-voice", str(artifacts.tts_voice_path)))
        if profile.source_name is not None:
            command.extend(("--voice-source", profile.source_name))
        if profile.sink_name is not None:
            command.extend(("--voice-sink", profile.sink_name))
        if profile.voice_turn_timing:
            command.append("--voice-turn-timing")
    return command


def _app_server_command(
    profile: LocalAlphaProfile,
    runtime: LocalAlphaRuntime,
) -> list[str]:
    return [
        runtime.codex_executable,
        "app-server",
        "--listen",
        f"unix://{profile.codex_app_server_upstream_socket}",
    ]


def _remote_tui_command(
    profile: LocalAlphaProfile,
    runtime: LocalAlphaRuntime,
) -> list[str]:
    command = [
        runtime.codex_executable,
        "--remote",
        f"unix://{profile.codex_app_server_socket}",
    ]
    if profile.yolo_enabled:
        return [*command, "--yolo"]
    return [
        *command,
        "--sandbox",
        profile.codex_sandbox,
        "--ask-for-approval",
        "on-request",
    ]


def _audio_check_command(
    profile: LocalAlphaProfile,
    uv_executable: str,
) -> list[str]:
    command = [
        uv_executable,
        "run",
        "--project",
        str(profile.runtime_project),
        "--locked",
        "--no-sync",
        "python",
        "-m",
        "deskhelm_bridge",
        "audio",
        "status",
        "--json",
        "--pw-cat-command-prefix",
        profile.pw_cat_command_prefix,
    ]
    if profile.source_name is not None:
        command.extend(("--source", profile.source_name))
    if profile.sink_name is not None:
        command.extend(("--sink", profile.sink_name))
    return command


def _ptt_command(profile: LocalAlphaProfile) -> list[str]:
    return [
        sys.executable,
        "-m",
        "deskhelm_bridge",
        "ptt",
        "--socket",
        str(profile.socket_path),
        "--input-device",
        str(profile.input_device),
        "--agent-id",
        profile.agent_id,
        "--session-id",
        profile.session_id,
        "--project-id",
        profile.project_id,
        "--client-id",
        f"{profile.client_id}-ptt",
        "--focused",
    ]


def _focus_session(profile: LocalAlphaProfile) -> None:
    client_id = f"{profile.client_id}-focus"
    issued_at = int(time.time() * 1000)
    command = ControlCommand(
        kind=ControlKind.FOCUS,
        agent_id=profile.agent_id,
        session_id=profile.session_id,
        project_id=profile.project_id,
        issued_by=client_id,
        issued_at=issued_at,
        expires_at=issued_at + 5_000,
        idempotency_key=f"alpha-focus:{profile.client_id}:{profile.session_id}",
        payload=FocusPayload(),
    )
    result = send_control_command(
        command, profile.socket_path, client_id=client_id
    )
    if result.status is not ControlResultStatus.ACCEPTED:
        raise RuntimeError("local alpha session could not be focused")


def _ensure_user_bridge(
    profile: LocalAlphaProfile,
    runtime: LocalAlphaRuntime,
) -> subprocess.Popen | None:
    """Start the user bridge when it is not already accepting connections.

    The returned process is the bridge this Alpha started. Callers must not
    stop it after a session attaches; other sessions share the process.
    """
    profile.socket_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = profile.socket_path.with_name(profile.socket_path.name + ".start.lock")
    with lock_path.open("a") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if _socket_accepts(profile.socket_path):
            _require_attachable_bridge(profile)
            return None
        if profile.socket_path.exists():
            profile.socket_path.unlink()
        process = subprocess.Popen(
            _bridge_command(profile, runtime),
            cwd=profile.repository_root,
            env=runtime.environment,
            start_new_session=True,
        )
        deadline = time.monotonic() + SESSION_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("user bridge exited during startup")
            if _socket_accepts(profile.socket_path):
                _require_attachable_bridge(profile)
                return process
            time.sleep(0.05)
        _stop_process(process)
        raise RuntimeError("user bridge did not become ready")


def _require_attachable_bridge(profile: LocalAlphaProfile) -> None:
    try:
        status = query_agent_runtime_directory(
            profile.socket_path,
            client_id=f"{profile.client_id}-runtime",
        )
    except ProtocolError as error:
        raise RuntimeError(
            "user bridge does not accept attached sessions"
        ) from error
    if profile.voice_enabled and not status.voice_enabled:
        raise RuntimeError(
            "user bridge is running without voice; "
            "stop it before the first voice session"
        )


def _socket_accepts(socket_path: Path) -> bool:
    if not socket_path.exists():
        return False
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(0.2)
        client.connect(str(socket_path))
        return True
    except OSError:
        return False
    finally:
        client.close()


def _attach_runtime(profile: LocalAlphaProfile, codex_version: str) -> None:
    status = attach_agent_runtime(
        profile.socket_path,
        client_id=f"{profile.client_id}-runtime",
        agent_id=profile.agent_id,
        session_id=profile.session_id,
        project_id=profile.project_id,
        source_version=codex_version,
        app_server_socket=profile.codex_app_server_upstream_socket,
        tui_proxy_socket=profile.codex_app_server_socket,
    )
    if status != "attached":
        raise RuntimeError(f"local alpha could not attach its runtime ({status})")


def _detach_runtime(profile: LocalAlphaProfile) -> None:
    try:
        detach_agent_runtime(
            profile.socket_path,
            client_id=f"{profile.client_id}-runtime",
            agent_id=profile.agent_id,
            session_id=profile.session_id,
            project_id=profile.project_id,
        )
    except (ConnectionError, OSError, ProtocolError, RuntimeError):
        return


def _register_session(
    profile: LocalAlphaProfile,
    codex_version: str,
    bridge: subprocess.Popen | None,
) -> AdapterSessionPublisher:
    deadline = time.monotonic() + SESSION_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if bridge is not None and bridge.poll() is not None:
            raise RuntimeError("local alpha Bridge exited during startup")
        publisher = AdapterSessionPublisher(
            profile.socket_path,
            client_id=f"{profile.client_id}-session",
            additional_capabilities=(INTERACTION_EVENT_V1_CAPABILITY,),
        )
        try:
            publisher.connect()
            result = publisher.publish(
                _session_event(
                    profile,
                    codex_version,
                    AdapterSessionAction.REGISTER,
                )
            )
        except (ConnectionError, OSError, ProtocolError, RuntimeError):
            publisher.close()
            time.sleep(0.05)
            continue
        if not profile.shared_bridge and result.slot != 0:
            publisher.close()
            raise RuntimeError("local alpha session did not receive slot zero")
        return publisher
    raise RuntimeError("local alpha Bridge did not become ready")


def _wait_for_owned_socket(
    socket_path: Path,
    process: subprocess.Popen | None,
    component: str,
) -> None:
    deadline = time.monotonic() + APP_SERVER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"local alpha {component} exited during startup")
        if socket_path.is_socket():
            return
        time.sleep(0.05)
    raise RuntimeError(f"local alpha {component} did not become ready")


def _release_session(
    profile: LocalAlphaProfile,
    codex_version: str,
    publisher: AdapterSessionPublisher,
) -> None:
    try:
        publisher.publish(
            _session_event(
                profile,
                codex_version,
                AdapterSessionAction.RELEASE,
            )
        )
    except (ConnectionError, OSError, ProtocolError, RuntimeError):
        pass


def _session_event(
    profile: LocalAlphaProfile,
    codex_version: str,
    action: AdapterSessionAction,
) -> AdapterSessionEvent:
    try:
        adapter_version = metadata.version("deskhelm")
    except metadata.PackageNotFoundError:
        adapter_version = "0.1.0.dev0"
    return AdapterSessionEvent(
        action=action,
        adapter_id="deskhelm-local-alpha",
        adapter_version=adapter_version,
        runtime_name="codex-cli",
        runtime_version=codex_version,
        agent_id=profile.agent_id,
        session_id=profile.session_id,
        project_id=profile.project_id,
        capabilities=(
            AdapterCapability.INTERACTION_EVENTS,
            AdapterCapability.APPROVAL_REQUESTS,
            AdapterCapability.SESSION_RESUME,
            AdapterCapability.SUBMIT_PROMPT,
            AdapterCapability.INTERRUPT,
            AdapterCapability.APPROVAL_DECISIONS,
        ),
        occurred_at=int(time.time() * 1000),
        preferred_slot=(
            None
            if profile.shared_bridge or action is not AdapterSessionAction.REGISTER
            else 0
        ),
    )


def _child_environment(
    profile: LocalAlphaProfile,
    environment: Mapping[str, str],
) -> dict[str, str]:
    child_environment = dict(environment)
    child_environment.pop("VIRTUAL_ENV", None)
    source_paths = [
        profile.repository_root / "bridge",
        profile.repository_root / "adapters" / "codex",
        profile.repository_root / "adapters" / "claude",
        profile.repository_root / "adapters" / "grok",
    ]
    if profile.voice_enabled:
        source_paths.append(profile.repository_root / "voice")
    existing = child_environment.get("PYTHONPATH")
    values = [str(path) for path in source_paths]
    if existing:
        values.append(existing)
    child_environment["PYTHONPATH"] = os.pathsep.join(values)
    return child_environment


def _voice_artifacts(profile: LocalAlphaProfile) -> LocalAlphaArtifacts:
    if not profile.voice_enabled or profile.artifacts is None:
        raise RuntimeError("local voice is not enabled")
    return profile.artifacts


def _detect_tts_provider(directory: Path) -> str:
    # Prefer IndexTTS when its standard model/reference layout is present.
    if _has_indextts_markers(directory):
        return "indextts"
    if (directory / PIPER_MODEL_NAME).is_file():
        return "piper"
    if (directory / KOKORO_MODEL_NAME).is_file():
        return "kokoro"
    raise ValueError("TTS directory contains no supported voice")


def _has_indextts_markers(directory: Path) -> bool:
    return (
        (directory / INDEXTTS_CONFIG_NAME).is_file()
        and (directory / INDEXTTS_VOICE_NAME).is_file()
    )


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)


def _stop_tui_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)


def _normalized_return_code(return_code: int) -> int:
    if return_code < 0:
        return 128 - return_code
    return return_code


def _has_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _capture_terminal_state() -> _TerminalState:
    try:
        file_descriptor = sys.stdin.fileno()
        return _TerminalState(
            file_descriptor=file_descriptor,
            foreground_process_group=os.tcgetpgrp(file_descriptor),
            attributes=termios.tcgetattr(file_descriptor),
        )
    except (OSError, termios.error, ValueError) as error:
        raise ValueError(
            "deskhelm codex --tui requires a usable interactive terminal"
        ) from error


def _set_terminal_foreground(file_descriptor: int, process_group: int) -> None:
    previous_sigttou = signal.getsignal(signal.SIGTTOU)
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        os.tcsetpgrp(file_descriptor, process_group)
    except OSError as error:
        raise RuntimeError("local alpha could not activate the Codex TUI") from error
    finally:
        signal.signal(signal.SIGTTOU, previous_sigttou)


def _restore_terminal(state: _TerminalState) -> None:
    previous_sigttou = signal.getsignal(signal.SIGTTOU)
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        os.tcsetpgrp(
            state.file_descriptor,
            state.foreground_process_group,
        )
        termios.tcsetattr(
            state.file_descriptor,
            termios.TCSADRAIN,
            state.attributes,
        )
    except (OSError, termios.error):
        pass
    finally:
        signal.signal(signal.SIGTTOU, previous_sigttou)


def _absolute_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise ValueError(f"{label} is unavailable")


def _require_directory(path: Path, label: str) -> None:
    if not path.is_dir():
        raise ValueError(f"{label} is unavailable")
