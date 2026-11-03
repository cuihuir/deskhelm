"""Run one managed Codex TUI as a protocol v2 session host (ADR 0043, ADR 0033).

The host owns this directory's Codex app-server, the TUI approval proxy, and
the remote TUI inside the PTY relay with the two-press Ctrl-C guard. Its
Bridge connection is the session lease, so a Bridge restart leaves Codex
running; the session reopens when the Bridge returns.
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import termios
import time
from typing import Any

from deskhelm_client import BridgeClient
from deskhelm_host.bridge_process import ensure_bridge
from deskhelm_host.link import BridgeLink
from deskhelm_protocol import ApprovalSupport, SessionKey
from deskhelm_protocol.paths import runtime_directory
from deskhelm_voice_host.launch import VOICE_ENVIRONMENT, voice_host_command

from .app_server import detect_codex_version
from .driver import CodexSession

START_TIMEOUT_SECONDS = 15.0
STOP_TIMEOUT_SECONDS = 5.0
OPEN_TIMEOUT_SECONDS = 10.0
VOICE_CHECK_SECONDS = 120.0
SANDBOXES = ("read-only", "workspace-write")
_RELAY = Path(__file__).resolve().parents[3] / "tools" / "run-with-pty.py"


class _Stopped(Exception):
    def __init__(self, signal_number: int) -> None:
        super().__init__(signal_number)
        self.signal_number = signal_number


def codex_sockets(digest: str) -> tuple[Path, Path]:
    """The app-server and TUI proxy sockets of one directory's Codex host.

    They differ from the v1 launcher's sockets, so both can run side by side.
    """
    root = runtime_directory()
    return root / f"codex-{digest}-app.sock", root / f"codex-{digest}-tui.sock"


def tui_command(codex: str, proxy_socket: Path, *, sandbox: str, yolo: bool) -> list[str]:
    command = [codex, "--remote", f"unix://{proxy_socket}"]
    if yolo:
        return [*command, "--yolo"]
    return [*command, "--sandbox", sandbox, "--ask-for-approval", "on-request"]


def run_codex_host(
    *,
    cwd: Path,
    session: SessionKey,
    digest: str,
    socket_path: Path,
    client_id: str,
    codex: str = "codex",
    sandbox: str = "read-only",
    yolo: bool = False,
    voice: list[str] | None = None,
) -> int:
    """Run the managed TUI; ``voice`` holds voice host arguments to start it too."""
    if sandbox not in SANDBOXES:
        raise ValueError(f"unknown Codex sandbox {sandbox!r}")
    if not _interactive_terminal():
        print("deskhelm host: codex needs an interactive terminal", file=sys.stderr)
        return 2
    try:
        version = detect_codex_version(codex)
    except RuntimeError as error:
        print(f"deskhelm host: {error}", file=sys.stderr)
        return 1
    app_socket, proxy_socket = codex_sockets(digest)
    for path in (app_socket, proxy_socket):
        if _accepts(path):
            print("deskhelm host: a Codex host already runs in this directory", file=sys.stderr)
            return 1
        path.unlink(missing_ok=True)
    if voice is not None and not check_voice(voice):
        # A missing microphone is found before the TUI takes the terminal.
        return 1

    driver = CodexSession(
        session,
        socket_path=app_socket,
        runtime_version=version,
        tui_proxy_socket_path=proxy_socket,
        approvals=ApprovalSupport.NONE if yolo else ApprovalSupport.ANSWERABLE,
    )
    link = BridgeLink(socket_path, session, driver, client_id=client_id, ensure_bridge=ensure_bridge)
    terminal = termios.tcgetattr(sys.stdin.fileno())
    stderr = sys.stderr
    app_server: subprocess.Popen[bytes] | None = None
    relay: subprocess.Popen[bytes] | None = None
    speech: subprocess.Popen[bytes] | None = None
    speech_noted = False
    log: Any = None
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {number: signal.getsignal(number) for number in signals}

    def stop(signal_number: int, _frame: object) -> None:
        raise _Stopped(signal_number)

    for number in signals:
        signal.signal(number, stop)
    code = 0
    try:
        app_server = subprocess.Popen(
            [codex, "app-server", "--listen", f"unix://{app_socket}"],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _wait_for_socket(app_socket, app_server, "the Codex app-server")
        link.start()
        # Facts sent before the session opens would be lost, so wait briefly.
        if not link.opened.wait(OPEN_TIMEOUT_SECONDS):
            print("deskhelm host: the Bridge is not reachable yet; continuing", file=sys.stderr)
        driver.start(link.emit)
        _wait_for_socket(proxy_socket, app_server, "the Codex TUI proxy")
        print(f"deskhelm host: codex session {session.session_id} on {socket_path}", file=sys.stderr)
        if voice is not None:
            # Starting this session with voice is the explicit choice to
            # speak to it, as `deskhelm codex --voice` always meant.
            _focus(socket_path, session, f"{client_id}-focus")
            speech = _start_voice(voice, runtime_directory() / f"codex-{digest}-voice.log")
        # The TUI owns the terminal now; diagnostics from host threads go to
        # an owner-only log instead of the screen.
        descriptor = os.open(
            runtime_directory() / f"codex-{digest}-host.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        log = os.fdopen(descriptor, "a", encoding="utf-8")
        sys.stderr = log
        relay = subprocess.Popen(
            [
                sys.executable,
                str(_RELAY),
                *redaction_arguments(),
                "--guard-ctrl-c",
                "--",
                *tui_command(codex, proxy_socket, sandbox=sandbox, yolo=yolo),
            ],
            cwd=cwd,
        )
        while True:
            returned = relay.poll()
            if returned is not None:
                code = returned
                break
            if app_server.poll() is not None:
                print("deskhelm host: the Codex app-server exited", file=sys.stderr)
                code = 1
                break
            if speech is not None and not speech_noted and speech.poll() is not None:
                # The TUI goes on without voice; the voice log says why.
                print(f"deskhelm host: the voice host stopped with code {speech.returncode}", file=sys.stderr)
                speech_noted = True
            time.sleep(0.1)
    except _Stopped as stopped:
        code = 128 + stopped.signal_number
    except (OSError, RuntimeError, ValueError) as error:
        print(f"deskhelm host: {error}", file=stderr)
        code = 1
    finally:
        for number in signals:
            signal.signal(number, signal.SIG_IGN)
        if relay is not None:
            _stop(relay, group=False)
        _restore_terminal(terminal)
        sys.stderr = stderr
        if log is not None:
            log.close()
        if speech is not None:
            _stop(speech, group=True)
            if speech_noted:
                print("deskhelm host: voice stopped early; see codex-*-voice.log in the runtime directory", file=sys.stderr)
        driver.close()
        link.close()
        if app_server is not None:
            _stop(app_server, group=True)
        for path in (app_socket, proxy_socket):
            path.unlink(missing_ok=True)
        for number, handler in previous.items():
            signal.signal(number, handler)
    return code


def _interactive_terminal() -> bool:
    # Under project-secrets stdout is a pipe; the relay draws on /dev/tty.
    if not sys.stdin.isatty():
        return False
    try:
        os.close(os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY))
    except OSError:
        return False
    return True


def redaction_arguments() -> list[str]:
    """Hide injected voice settings from the TUI output, as the v1 relay did."""
    arguments: list[str] = []
    for name in VOICE_ENVIRONMENT:
        if os.environ.get(name):
            arguments.extend(("--redact-env", name))
    return arguments


def check_voice(arguments: list[str]) -> bool:
    """Run the voice host preflight; its messages name settings, never values."""
    try:
        command, environment = voice_host_command([*arguments, "--check"])
        checked = subprocess.run(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=VOICE_CHECK_SECONDS,
            check=False,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"deskhelm host: the voice check did not finish: {error}", file=sys.stderr)
        return False
    if checked.returncode != 0:
        sys.stderr.write(checked.stderr)
    return checked.returncode == 0


def _start_voice(arguments: list[str], log_path: Path) -> subprocess.Popen[bytes]:
    command, environment = voice_host_command(arguments)
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        return subprocess.Popen(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=descriptor,
            start_new_session=True,
        )
    finally:
        os.close(descriptor)


def _focus(socket_path: Path, session: SessionKey, client_id: str) -> None:
    client = BridgeClient(socket_path, client_id=client_id)
    client.start()
    try:
        if client.wait_ready(OPEN_TIMEOUT_SECONDS):
            client.command("focus", session)
    finally:
        client.close()


def _accepts(path: Path) -> bool:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        probe.connect(str(path))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _wait_for_socket(path: Path, process: subprocess.Popen[bytes], component: str) -> None:
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{component} exited during startup")
        if path.is_socket():
            return
        time.sleep(0.05)
    raise RuntimeError(f"{component} did not start")


def _stop(process: subprocess.Popen[bytes], *, group: bool) -> None:
    """Stop a child: the relay alone, or the app-server's process group."""
    if process.poll() is not None:
        return
    first, last = (signal.SIGINT, signal.SIGKILL) if group else (signal.SIGTERM, signal.SIGKILL)
    for signal_number in (first, last):
        try:
            if group:
                os.killpg(process.pid, signal_number)
            else:
                process.send_signal(signal_number)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def _restore_terminal(attributes: list[Any]) -> None:
    # A relay that was killed leaves the terminal raw.
    try:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, attributes)
    except (OSError, termios.error, ValueError):
        pass
