"""Small protocol v2 commands: ``python -m deskhelm_client sessions|focus``."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from deskhelm_protocol import SessionKey
from deskhelm_protocol.paths import default_socket_path

from .connection import BridgeClient
from .texts import activity_text, outcome_text, result_text

_READY_SECONDS = 5.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deskhelm-client", description="Read or steer the protocol v2 Bridge.")
    parser.add_argument("--socket", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sessions", help="list the sessions and their pending approvals")
    focus = commands.add_parser("focus", help="mark one active session as the Bridge focus")
    for name in ("--agent-id", "--session-id", "--project-id"):
        focus.add_argument(name, required=True)
    args = parser.parse_args(argv)

    client = BridgeClient(args.socket or default_socket_path(), client_id="deskhelm-cli")
    client.start()
    try:
        if not client.wait_ready(_READY_SECONDS):
            print(f"deskhelm client: {result_text('bridge_unavailable')}", file=sys.stderr)
            return 1
        if args.command == "focus":
            key = SessionKey(args.agent_id, args.session_id, args.project_id)
            result = client.command("focus", key)
            print(result_text(result.code))
            return 0 if result.accepted else 1
        state = client.console()
        if not state.sessions:
            print("没有会话")
        for key, view in state.sessions.items():
            marker = "*" if key == state.focus else " "
            runtime = view.get("runtime", {}).get("runtime_name", "")
            line = f"{marker} {key.agent_id}/{key.session_id}/{key.project_id}  {runtime}  {activity_text(view)}"
            outcome = outcome_text(view.get("last_outcome"))
            if outcome and view.get("activity") == "idle":
                line += f"  {outcome}"
            print(line)
            for ticket in view.get("approvals", []):
                print(f"    {ticket['kind']} {ticket['approval_id']}: {ticket['summary']}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
