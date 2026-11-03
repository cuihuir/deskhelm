"""Grok Build flavor of the generic ACP driver."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from deskhelm_protocol import ApprovalSupport

from .driver import AcpFlavor


class GrokFlavor(AcpFlavor):
    adapter_id = "deskhelm-grok"
    runtime_name = "grok"

    def __init__(self, leader_socket: Path) -> None:
        self.leader_socket = leader_socket

    def argv(self) -> list[str]:
        return ["grok", "agent", "--no-leader", "--leader-socket", str(self.leader_socket), "stdio"]

    def approvals_from_extension(
        self, method: str, params: dict[str, Any], vendor_session: str
    ) -> ApprovalSupport | None:
        # Grok in always-approve ("yolo") mode answers permissions itself and
        # never asks the client, so approvals are not answerable here.
        if method != "_x.ai/sessions/changed" or not vendor_session:
            return None
        for item in params.get("upserted") or []:
            if isinstance(item, dict) and item.get("sessionId") == vendor_session and isinstance(item.get("yolo"), bool):
                return ApprovalSupport.NONE if item["yolo"] else ApprovalSupport.ANSWERABLE
        return None


def grok_version() -> str:
    try:
        completed = subprocess.run(["grok", "--version"], check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return "grok"
    line = completed.stdout.strip().splitlines()
    return line[0].strip()[:128] if completed.returncode == 0 and line and line[0].strip() else "grok"
