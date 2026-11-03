from __future__ import annotations

import json
from pathlib import Path
import subprocess


def grok_argv(leader_socket: Path) -> list[str]:
    """Start one Grok stdio agent. The prompt is never an argument."""
    return [
        "grok",
        "agent",
        "--no-leader",
        "--leader-socket",
        str(leader_socket),
        "stdio",
    ]


def grok_preamble(cwd: Path) -> tuple[bytes, bytes]:
    """Vendor handshake. The working directory stays in this process."""
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
            "clientInfo": {"name": "deskhelm", "version": "0.1"},
        },
    }
    session_new = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "session/new",
        "params": {"cwd": str(cwd), "mcpServers": []},
    }
    return (
        _line(initialize),
        _line(session_new),
    )


def runtime_version() -> str:
    try:
        completed = subprocess.run(
            ["grok", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "grok"
    line = completed.stdout.strip().splitlines()
    if completed.returncode != 0 or not line:
        return "grok"
    text = line[0].strip()
    return text[:128] if text else "grok"


def _line(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n"
