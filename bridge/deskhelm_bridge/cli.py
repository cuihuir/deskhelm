from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from .client import send_event
from .event import AgentEvent, AgentState, ProtocolError
from .paths import default_socket_path
from .server import run_bridge


def add_socket_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--socket", type=Path, default=default_socket_path())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deskhelm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bridge = subparsers.add_parser("bridge", help="run the local status bridge")
    add_socket_argument(bridge)
    bridge.add_argument("--slots", type=int, default=4)
    bridge.add_argument("--plain", action="store_true", help="print one line per event")
    bridge.add_argument("--no-color", action="store_true")
    bridge.add_argument("--max-events", type=int)
    bridge.add_argument("--max-connections", type=int, default=16)
    bridge.add_argument("--max-subscribers", type=int)
    bridge.add_argument("--subscriber-queue-frames", type=int, default=8)
    bridge.add_argument("--control-idempotency-entries", type=int, default=1024)
    bridge.add_argument(
        "--control-idempotency-retention-ms", type=int, default=300_000
    )
    bridge.add_argument("--control-approval-records", type=int, default=1024)
    bridge.add_argument(
        "--agent-provider",
        choices=("none", "codex"),
        default="none",
        help="enable a text-only Agent provider",
    )
    bridge.add_argument("--agent-workdir", type=Path, default=Path.cwd())
    bridge.add_argument("--agent-max-active-runs", type=int, default=4)
    bridge.add_argument("--agent-session-records", type=int, default=64)
    bridge.add_argument("--agent-run-timeout-seconds", type=float, default=300.0)
    bridge.add_argument("--codex-executable", default="codex")
    bridge.add_argument(
        "--codex-sandbox",
        choices=("read-only", "workspace-write"),
        default="read-only",
    )

    emit = subparsers.add_parser("emit", help="send one normalized event")
    add_socket_argument(emit)
    emit.add_argument("--agent-id", required=True)
    emit.add_argument("--slot", type=int, required=True)
    emit.add_argument("--state", choices=[state.value for state in AgentState], required=True)
    emit.add_argument("--label", default="")
    emit.add_argument("--progress", type=float)

    simulate = subparsers.add_parser("simulate", help="cycle four demo agents through states")
    add_socket_argument(simulate)
    simulate.add_argument("--delay", type=float, default=0.35)
    simulate.add_argument("--cycles", type=int, default=1)
    return parser


def emit_event(args: argparse.Namespace) -> None:
    event = AgentEvent(
        agent_id=args.agent_id,
        slot=args.slot,
        state=AgentState(args.state),
        label=args.label,
        progress=args.progress,
    )
    send_event(event, args.socket)


def simulate(args: argparse.Namespace) -> None:
    agents = [
        ("frontend", 0),
        ("backend", 1),
        ("tests", 2),
        ("review", 3),
    ]
    states = [
        AgentState.IDLE,
        AgentState.THINKING,
        AgentState.RUNNING_TOOL,
        AgentState.WAITING_APPROVAL,
        AgentState.COMPLETED,
    ]
    for _ in range(args.cycles):
        for state_index, state in enumerate(states):
            for agent_id, slot in agents:
                progress = state_index / (len(states) - 1)
                send_event(
                    AgentEvent(
                        agent_id=f"demo:{agent_id}",
                        slot=slot,
                        state=state,
                        label=agent_id,
                        progress=progress,
                    ),
                    args.socket,
                )
            time.sleep(args.delay)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "bridge":
            if args.slots < 1:
                raise ValueError("--slots must be at least 1")
            agent_provider = None
            if args.agent_provider == "codex":
                try:
                    from deskhelm_codex_adapter import CodexExecProvider
                except ModuleNotFoundError as error:
                    if error.name != "deskhelm_codex_adapter":
                        raise
                    from adapters.codex.deskhelm_codex_adapter import (
                        CodexExecProvider,
                    )

                agent_provider = CodexExecProvider(
                    command_prefix=(args.codex_executable,),
                    sandbox=args.codex_sandbox,
                    timeout_seconds=args.agent_run_timeout_seconds,
                )
            return_code = run_bridge(
                socket_path=args.socket,
                slot_count=args.slots,
                stream=sys.stdout,
                color=not args.no_color,
                live=not args.plain,
                max_events=args.max_events,
                max_connections=args.max_connections,
                max_subscribers=args.max_subscribers,
                subscriber_queue_frames=args.subscriber_queue_frames,
                control_idempotency_entries=args.control_idempotency_entries,
                control_idempotency_retention_ms=(
                    args.control_idempotency_retention_ms
                ),
                control_approval_records=args.control_approval_records,
                agent_provider=agent_provider,
                agent_working_directory=args.agent_workdir,
                agent_max_active_runs=args.agent_max_active_runs,
                agent_session_records=args.agent_session_records,
            )
            return 0 if return_code >= 0 else 1
        if args.command == "emit":
            emit_event(args)
            return 0
        if args.command == "simulate":
            simulate(args)
            return 0
    except (ConnectionError, ProtocolError, ValueError) as error:
        print(f"deskhelm: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
