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

from .bridge_process import bridge_protocol, ensure_bridge
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


def session_for(agent: str, cwd: Path, project_id: str | None = None, session_id: str | None = None) -> tuple[SessionKey, str]:
    """The session a host in ``cwd`` opens, and the directory digest."""
    digest = hashlib.sha256(str(cwd).encode("utf-8")).hexdigest()[:12]
    return SessionKey(agent, session_id or f"local-{digest}", project_id or cwd.name), digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deskhelm-host", description="Run one agent session host here.")
    parser.add_argument("agent", choices=("claude", "codex", "grok"))
    parser.add_argument("--socket", type=Path, default=None)
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--agent-workdir", type=Path, default=None, help="the project directory; defaults to here")
    codex = parser.add_argument_group("codex")
    codex.add_argument("--codex-sandbox", choices=("read-only", "workspace-write"), default="read-only")
    codex.add_argument("--yolo", action="store_true", help="full access and no approvals; extremely dangerous")
    codex.add_argument("--voice", action="store_true", help="also attach local voice and focus this session")
    codex.add_argument("--default-source", action="store_true", help="voice: use the default microphone")
    codex.add_argument("--codex-executable", default="codex")
    codex.add_argument("--check", action="store_true", help="check Codex, the Bridge, and voice, then exit")
    # The managed TUI always runs in this terminal; the flag stays accepted.
    codex.add_argument("--tui", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    codex_only = (args.yolo, args.voice, args.default_source, args.check, args.tui, args.codex_sandbox != "read-only")
    if args.agent != "codex" and any(codex_only):
        parser.error("the codex options apply to codex only")
    if args.default_source and not args.voice:
        parser.error("--default-source needs --voice")

    cwd = (args.agent_workdir or Path.cwd()).expanduser().resolve()
    session, digest = session_for(args.agent, cwd, args.project_id, args.session_id)
    socket_path = args.socket or default_socket_path()
    runtime_directory().mkdir(mode=0o700, parents=True, exist_ok=True)
    if bridge_protocol(socket_path) == "other":
        print(f"deskhelm host: {socket_path} belongs to something other than a v2 Bridge, "
              "such as a v1 Bridge; stop it first", file=sys.stderr)
        return 1
    if args.agent == "codex":
        _adapter_path("codex")
        from deskhelm_codex_adapter.host import check_voice, run_codex_host

        voice = None
        if args.voice:
            voice = ["--socket", str(socket_path), *(["--default-source"] if args.default_source else [])]
        if args.check:
            return _check_codex(args.codex_executable, voice, check_voice)
        return run_codex_host(
            cwd=cwd,
            session=session,
            digest=digest,
            socket_path=socket_path,
            client_id=f"deskhelm-host-codex-{digest}",
            codex=args.codex_executable,
            sandbox=args.codex_sandbox,
            yolo=args.yolo,
            voice=voice,
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


def _check_codex(codex: str, voice: list[str] | None, check_voice) -> int:
    from deskhelm_codex_adapter import detect_codex_version

    try:
        version = detect_codex_version(codex)
    except RuntimeError as error:
        print(f"deskhelm host: {error}", file=sys.stderr)
        return 1
    print(f"deskhelm host: {version} found", file=sys.stderr)
    if voice is not None and not check_voice(voice):
        return 1
    print("deskhelm host: codex preflight passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
