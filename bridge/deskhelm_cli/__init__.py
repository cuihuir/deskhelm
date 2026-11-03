"""The ``deskhelm`` command (ADR 0043).

Each command runs one protocol v2 session host or surface against the user
Bridge at ``bridge.sock``; ``bridge`` runs the Bridge itself in the
foreground. ``alpha`` remains a hidden alias of ``codex``.
"""

from __future__ import annotations

from collections.abc import Callable
import sys

USAGE = """usage: deskhelm <command> [options]

commands:
  codex       run the managed Codex TUI here as one session (--voice adds voice)
  agent       run Claude or Grok here as one session: deskhelm agent claude|grok
  web         serve the page for the Bridge on http://127.0.0.1:8766
  dashboard   watch and steer the sessions in this terminal
  sessions    list the sessions and their pending approvals
  focus       set the session that voice speaks to
  voice       attach local voice to the running Bridge
  simulate    open a demo session that answers prompts with a scripted turn
  bridge      run the Bridge in the foreground; hosts start it on demand
  audio       inspect or test the local audio devices

Run `deskhelm <command> --help` for its options.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE, end="", file=sys.stdout if argv else sys.stderr)
        return 0 if argv else 2
    handler = _COMMANDS.get(argv[0])
    if handler is None:
        print(f"deskhelm: unknown command {argv[0]!r}\n", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        return 2
    return handler(argv[1:])


def _codex(rest: list[str]) -> int:
    from deskhelm_host.__main__ import main as host_main

    return host_main(["codex", *rest])


def _agent(rest: list[str]) -> int:
    from deskhelm_host.__main__ import main as host_main

    return host_main(rest)


def _web(rest: list[str]) -> int:
    from deskhelm_web.__main__ import main as web_main

    return web_main(rest)


def _dashboard(rest: list[str]) -> int:
    from deskhelm_dashboard.__main__ import main as dashboard_main

    return dashboard_main(rest)


def _client(command: str) -> Callable[[list[str]], int]:
    def run(rest: list[str]) -> int:
        from deskhelm_client.__main__ import main as client_main

        return client_main(_options_first(command, rest))

    return run


def _voice(rest: list[str]) -> int:
    from deskhelm_voice_host.launch import exec_voice_host

    return exec_voice_host(rest)


def _simulate(rest: list[str]) -> int:
    from deskhelm_host.simulate import main as simulate_main

    return simulate_main(rest)


def _bridge(rest: list[str]) -> int:
    from deskhelm_core.__main__ import main as core_main

    return core_main(rest)


def _audio(rest: list[str]) -> int:
    from .audio import main as audio_main

    return audio_main(rest)


def _options_first(command: str, rest: list[str]) -> list[str]:
    """``deskhelm focus --socket S …`` keeps working: ``--socket`` goes first."""
    leading: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(rest):
        item = rest[index]
        if item == "--socket" and index + 1 < len(rest):
            leading.extend(rest[index : index + 2])
            index += 2
            continue
        if item.startswith("--socket="):
            leading.append(item)
        else:
            remaining.append(item)
        index += 1
    return [*leading, command, *remaining]


_COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "codex": _codex,
    "alpha": _codex,
    "agent": _agent,
    "web": _web,
    "dashboard": _dashboard,
    "sessions": _client("sessions"),
    "focus": _client("focus"),
    "voice": _voice,
    "simulate": _simulate,
    "bridge": _bridge,
    "audio": _audio,
}
