from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys

from .adapter import AdapterCapability, AdapterSessionAction, AdapterSessionEvent
from .client import AdapterSessionPublisher, attach_relay_runtime, detach_agent_runtime
from .event import ProtocolError
from .paths import alpha_directory_digest, deskhelm_runtime_directory
from .stdio_relay import StdioRelay
import time


_CAPABILITIES = (
    AdapterCapability.INTERACTION_EVENTS,
    AdapterCapability.TOOL_EVENTS,
    AdapterCapability.APPROVAL_REQUESTS,
    AdapterCapability.SUBMIT_PROMPT,
    AdapterCapability.INTERRUPT,
    AdapterCapability.APPROVAL_DECISIONS,
)


def run_agent_session(args) -> int:
    """Start Claude or Grok in the current directory and attach it."""
    workdir = Path.cwd()
    project_id = args.project_id or workdir.name
    _require_identity(project_id, "project_id")
    digest = alpha_directory_digest(workdir)
    session_id = args.session_id or f"local-{digest}"
    _require_identity(session_id, "session_id")
    kind = args.runtime
    agent_id = kind
    runtime_dir = deskhelm_runtime_directory()
    relay_path = runtime_dir / f"agent-{kind}-{digest}.sock"
    if kind == "claude":
        _ensure_adapter_path("claude")
        from deskhelm_claude_adapter.command import claude_argv, runtime_version

        command = claude_argv()
        source_version = runtime_version()
        runtime_name = "claude-code"
        adapter_id = "deskhelm-claude"
        runtime_kind = "claude_stream"
        preamble: tuple[bytes, ...] = ()
    elif kind == "grok":
        _ensure_adapter_path("grok")
        from deskhelm_grok_adapter.command import grok_argv, grok_preamble, runtime_version

        leader = runtime_dir / f"grok-leader-{digest}.sock"
        command = grok_argv(leader)
        source_version = runtime_version()
        runtime_name = "grok"
        adapter_id = "deskhelm-grok"
        runtime_kind = "grok_stdio"
        preamble = grok_preamble(workdir)
    else:
        raise ValueError("agent runtime is invalid")
    client_id = args.client_id
    if client_id == "deskhelm-agent":
        client_id = f"{client_id}-{kind}-{digest}"
    relay = StdioRelay(relay_path, command, workdir, preamble)
    relay.serve()
    publisher: AdapterSessionPublisher | None = None
    attached = False
    try:
        publisher = AdapterSessionPublisher(
            args.socket,
            client_id=client_id,
            additional_capabilities=("interaction_event_v1",),
        )
        publisher.connect()
        publisher.publish(
            AdapterSessionEvent(
                action=AdapterSessionAction.REGISTER,
                adapter_id=adapter_id,
                adapter_version=_adapter_version(),
                runtime_name=runtime_name,
                runtime_version=source_version,
                agent_id=agent_id,
                session_id=session_id,
                project_id=project_id,
                capabilities=_CAPABILITIES,
                occurred_at=int(time.time() * 1000),
            )
        )
        status = attach_relay_runtime(
            args.socket,
            client_id=f"{client_id}-attach",
            agent_id=agent_id,
            session_id=session_id,
            project_id=project_id,
            source_version=source_version,
            runtime_kind=runtime_kind,
            relay_socket=relay_path,
        )
        if status != "attached":
            raise RuntimeError(f"local agent could not attach its runtime ({status})")
        attached = True
        code = relay.wait()
        return 0 if code == 0 else 1
    finally:
        if attached:
            try:
                detach_agent_runtime(
                    args.socket,
                    client_id=f"{client_id}-detach",
                    agent_id=agent_id,
                    session_id=session_id,
                    project_id=project_id,
                )
            except (ConnectionError, OSError, ProtocolError, RuntimeError):
                pass
        if publisher is not None:
            try:
                publisher.publish(
                    AdapterSessionEvent(
                        action=AdapterSessionAction.RELEASE,
                        adapter_id=adapter_id,
                        adapter_version=_adapter_version(),
                        runtime_name=runtime_name,
                        runtime_version=source_version,
                        agent_id=agent_id,
                        session_id=session_id,
                        project_id=project_id,
                        capabilities=_CAPABILITIES,
                        occurred_at=int(time.time() * 1000),
                    )
                )
            except (ConnectionError, OSError, ProtocolError, RuntimeError):
                pass
            publisher.close()
        relay.close()


def _adapter_version() -> str:
    try:
        return version("deskhelm")
    except PackageNotFoundError:
        return "0.1.0.dev0"


def _ensure_adapter_path(kind: str) -> None:
    root = Path(__file__).resolve().parents[2]
    adapter = root / "adapters" / kind
    if adapter.is_dir() and str(adapter) not in sys.path:
        sys.path.insert(0, str(adapter))


def _require_identity(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} is invalid")
