from __future__ import annotations

import os
import hashlib
from pathlib import Path
import re


_ALPHA_SOCKET_NAME = re.compile(r"^alpha-.+-[0-9a-f]{12}\.sock$")


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "deskhelm" / "bridge.sock"
    return Path("/tmp") / f"deskhelm-{os.getuid()}" / "bridge.sock"


def deskhelm_runtime_directory() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(runtime_dir) if runtime_dir else Path("/tmp") / f"deskhelm-{os.getuid()}"
    return root / "deskhelm"


def alpha_directory_digest(directory: Path | None = None) -> str:
    """Return the 12-character directory identity used for Alpha sockets."""
    resolved = (directory or Path.cwd()).expanduser().resolve()
    return hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]


def default_alpha_socket_path(directory: Path | None = None) -> Path:
    """Return a stable Alpha endpoint scoped to one working directory."""
    directory = (directory or Path.cwd()).expanduser().resolve()
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", directory.name) or "project"
    digest = alpha_directory_digest(directory)
    return deskhelm_runtime_directory() / f"alpha-{slug}-{digest}.sock"


def alpha_codex_socket_paths(stem: Path) -> tuple[Path, Path]:
    """Return the TUI proxy and app-server sockets derived from one stem."""
    proxy = stem.with_name(f"{stem.stem}-codex{stem.suffix}")
    upstream = proxy.with_name(f"{proxy.stem}-upstream{proxy.suffix}")
    return proxy, upstream


def running_alpha_sockets() -> list[Path]:
    """Return live Alpha Bridge sockets, excluding Codex proxy sockets."""
    root = deskhelm_runtime_directory()
    if not root.is_dir():
        return []
    found = [
        path
        for path in root.glob("alpha-*.sock")
        if path.is_socket() and _ALPHA_SOCKET_NAME.fullmatch(path.name) is not None
    ]
    return sorted(found)


def resolve_web_socket(explicit: Path | None, directory: Path | None = None) -> Path:
    """Choose the Bridge socket for the local web client.

    An explicit path wins. An empty path is rejected. Otherwise attach to the
    user bridge. Every DeskHelm session on this machine is registered there.
    `directory` is unused; per-directory Alpha sockets are no longer the page.
    """
    del directory
    if explicit is not None:
        if not str(explicit).strip():
            raise ValueError("web socket path is empty")
        return explicit.expanduser()
    return default_socket_path()
