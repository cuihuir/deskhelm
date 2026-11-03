"""Where local protocol v2 peers find each other."""

from __future__ import annotations

import os
from pathlib import Path


def runtime_directory() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(runtime_dir) if runtime_dir else Path("/tmp") / f"deskhelm-{os.getuid()}"
    return root / "deskhelm"


def default_socket_path() -> Path:
    # Until the v1 Bridge is removed, v2 listens beside it.
    return runtime_directory() / "bridge-v2.sock"
