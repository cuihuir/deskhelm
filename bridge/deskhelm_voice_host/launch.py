"""Start the voice host in the voice runtime from the root environment.

The root environment has no model providers, so ``deskhelm voice`` and
``deskhelm codex --voice`` run the host through ``uv`` in
``voice/runtime``. Importing this module never imports the voice library.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
# The names ``deskhelm codex --voice`` injects through project-secrets; the
# managed TUI's relay redacts their values.
VOICE_ENVIRONMENT = (
    "DESKHELM_ALPHA_VOICE_SOURCE",
    "DESKHELM_ALPHA_VOICE_SINK",
    "DESKHELM_ALPHA_ASR_MODEL_DIRECTORY",
    "DESKHELM_ALPHA_TTS_DIRECTORY",
)


def voice_host_command(arguments: list[str], *, uv: str | None = None) -> tuple[list[str], dict[str, str]]:
    executable = uv or shutil.which("uv")
    if not executable:
        raise RuntimeError("uv is required to run the voice host")
    command = [
        executable,
        "run",
        "--project",
        str(ROOT / "voice" / "runtime"),
        "--locked",
        "--no-sync",
        "python",
        "-m",
        "deskhelm_voice_host",
        *arguments,
    ]
    # The voice runtime installs this checkout, so no path is added. An
    # inherited VIRTUAL_ENV names the root environment and only draws a warning.
    environment = dict(os.environ)
    environment.pop("VIRTUAL_ENV", None)
    return command, environment


def exec_voice_host(arguments: list[str]) -> int:
    try:
        command, environment = voice_host_command(arguments)
    except RuntimeError as error:
        print(f"deskhelm voice: {error}", file=sys.stderr)
        return 1
    os.execvpe(command[0], command, environment)
    return 1
