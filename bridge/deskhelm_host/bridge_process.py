"""Start the user's Bridge when a host or surface finds it missing."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import deskhelm_core
import deskhelm_protocol
from deskhelm_protocol import PROTOCOL_VERSION, FrameDecoder, WireError, encode_frame
from deskhelm_protocol.paths import runtime_directory


def bridge_running(socket_path: Path) -> bool:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(socket_path))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def bridge_protocol(socket_path: Path, *, timeout: float = 2.0) -> str:
    """``v2``, ``none`` when nothing listens, or ``other`` (such as a v1 Bridge)."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        probe.connect(str(socket_path))
    except OSError:
        probe.close()
        return "none"
    hello = {
        "type": "hello",
        "protocol_version": PROTOCOL_VERSION,
        "role": "client",
        "client_id": "deskhelm-probe",
        "tier": "observer",
    }
    try:
        probe.sendall(encode_frame(hello))
        decoder = FrameDecoder()
        while True:
            data = probe.recv(65536)
            if not data:
                return "other"
            frames = decoder.feed(data)
            if frames:
                return "v2" if frames[0].get("type") == "hello_ok" else "other"
    except (OSError, WireError):
        # A v1 Bridge answers a v2 hello with a frame that has no type.
        return "other"
    finally:
        probe.close()


def ensure_bridge(socket_path: Path, *, timeout: float = 5.0) -> int | None:
    """Return the pid of a Bridge this call started, or None if one was running.

    Two callers may race; the Bridge's lock file lets exactly one serve.
    """
    state = bridge_protocol(socket_path)
    if state == "v2":
        return None
    if state == "other":
        raise RuntimeError(f"{socket_path} belongs to something other than a v2 Bridge; stop it first")
    roots = {
        str(Path(deskhelm_core.__file__).resolve().parents[1]),
        str(Path(deskhelm_protocol.__file__).resolve().parents[1]),
    }
    existing = os.environ.get("PYTHONPATH", "")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([*sorted(roots), *filter(None, [existing])])}
    log_dir = runtime_directory()
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(log_dir / "bridge.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "deskhelm_core", "--socket", str(socket_path)],
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=descriptor,
            env=env,
            start_new_session=True,
        )
    finally:
        os.close(descriptor)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bridge_running(socket_path):
            return process.pid
        if process.poll() is not None and bridge_running(socket_path):
            return None
        time.sleep(0.05)
    raise RuntimeError("the Bridge did not start")
