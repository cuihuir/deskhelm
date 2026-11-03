from __future__ import annotations

import subprocess


def claude_argv() -> list[str]:
    """Start one long-lived Claude stream. The prompt is never an argument."""
    return [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode",
        "default",
        "--permission-prompts",
        "host",
        # Without this, -p never sends can_use_tool and silently denies every
        # tool that needs permission (Claude Code 2.1.312).
        "--permission-prompt-tool",
        "stdio",
        "--strict-mcp-config",
    ]


def runtime_version() -> str:
    try:
        completed = subprocess.run(
            ["claude", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "claude-code"
    line = completed.stdout.strip().splitlines()
    if completed.returncode != 0 or not line:
        return "claude-code"
    text = line[0].strip()
    return text[:128] if text else "claude-code"
