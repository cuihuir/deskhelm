"""Run one session host in the current directory.

``python -m deskhelm_host claude|codex|grok [--socket PATH]`` starts the
vendor here, opens its session on the Bridge (starting the Bridge if needed),
and runs until the vendor exits. ``codex`` runs the managed Codex TUI in this
terminal. The working directory never crosses the protocol; only an opaque
project id and a directory digest do.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import signal
import sys
from typing import Any

from deskhelm_protocol import SessionKey
from deskhelm_protocol.paths import default_socket_path, runtime_directory

from .bridge_process import ensure_bridge
from .link import BridgeLink

_ROOT = Path(__file__).resolve().parents[2]
_OPEN_TIMEOUT_SECONDS = 10.0


def _adapter_path(name: str) -> None:
    path = str(_ROOT / "adapters" / name)
    if path not in sys.path:
        sys.path.insert(0, path)


def build_driver(kind: str, cwd: Path, digest: str) -> Any:
    if kind == "claude":
        _adapter_path("claude")
        from deskhelm_claude_adapter.command import runtime_version
        from deskhelm_claude_adapter.driver import ClaudeSession

        return ClaudeSession(cwd, runtime_version=runtime_version())
    if kind == "grok":
        _adapter_path("acp")
        from deskhelm_acp_adapter import AcpSession, GrokFlavor, grok_version

        leader = runtime_directory() / f"grok-leader-{digest}.sock"
        return AcpSession(GrokFlavor(leader), cwd, runtime_version=grok_version())
    raise ValueError(f"unknown agent {kind!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deskhelm-host", description="Run one agent session host here.")
    parser.add_argument("agent", choices=("claude", "codex", "grok"))
    parser.add_argument("--socket", type=Path, default=None)
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--codex-sandbox", choices=("read-only", "workspace-write"), default="read-only")
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="codex only: full access and no approvals; extremely dangerous",
    )
    args = parser.parse_args(argv)
    if args.agent != "codex" and (args.yolo or args.codex_sandbox != "read-only"):
        parser.error("--codex-sandbox and --yolo apply to codex only")

    cwd = Path.cwd().resolve()
    digest = hashlib.sha256(str(cwd).encode("utf-8")).hexdigest()[:12]
    session = SessionKey(args.agent, args.session_id or f"local-{digest}", args.project_id or cwd.name)
    socket_path = args.socket or default_socket_path()
    runtime_directory().mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.agent == "codex":
        _adapter_path("codex")
        from deskhelm_codex_adapter.host import run_codex_host

        return run_codex_host(
            cwd=cwd,
            session=session,
            digest=digest,
            socket_path=socket_path,
            client_id=f"deskhelm-host-codex-{digest}",
            sandbox=args.codex_sandbox,
            yolo=args.yolo,
        )

    driver = build_driver(args.agent, cwd, digest)
    link = BridgeLink(
        socket_path,
        session,
        driver,
        client_id=f"deskhelm-host-{args.agent}-{digest}",
        ensure_bridge=ensure_bridge,
    )
    link.start()
    # Facts sent before the session opens would be lost, so wait briefly.
    if not link.opened.wait(_OPEN_TIMEOUT_SECONDS):
        print("deskhelm host: the Bridge is not reachable yet; continuing", file=sys.stderr)

    def stop(signum, frame) -> None:
        driver.close()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        driver.start(link.emit)
    except OSError as error:
        print(f"deskhelm host: cannot start {args.agent}: {error.strerror or error}", file=sys.stderr)
        link.close()
        return 1
    print(f"deskhelm host: {args.agent} session {session.session_id} on {socket_path}", file=sys.stderr)
    try:
        code = driver.wait()
    finally:
        link.close()
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
