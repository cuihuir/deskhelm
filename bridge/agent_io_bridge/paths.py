from __future__ import annotations

import os
from pathlib import Path


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "agent-io" / "bridge.sock"
    return Path("/tmp") / f"agent-io-{os.getuid()}" / "bridge.sock"
