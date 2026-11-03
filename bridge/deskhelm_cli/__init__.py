"""Route the user's ``deskhelm`` commands to protocol v2 (ADR 0043 switch-over).

``codex`` (and its ``alpha`` alias), ``agent``, ``web``, ``dashboard``,
``focus``, ``sessions``, and ``voice`` run the v2 hosts and surfaces against
``bridge.sock``. Every other command returns ``None`` here and stays with the
v1 command line until phase 4 removes it.
"""

from __future__ import annotations

COMMANDS = frozenset({"codex", "alpha", "agent", "web", "dashboard", "focus", "sessions", "voice"})


def main(argv: list[str]) -> int | None:
    if not argv or argv[0] not in COMMANDS:
        return None
    command, rest = argv[0], list(argv[1:])
    if command in ("codex", "alpha"):
        from deskhelm_host.__main__ import main as host_main

        return host_main(["codex", *rest])
    if command == "agent":
        from deskhelm_host.__main__ import main as host_main

        return host_main(rest)
    if command == "web":
        from deskhelm_web.__main__ import main as web_main

        return web_main(rest)
    if command == "dashboard":
        from deskhelm_dashboard.__main__ import main as dashboard_main

        return dashboard_main(rest)
    if command in ("focus", "sessions"):
        from deskhelm_client.__main__ import main as client_main

        return client_main(_options_first(command, rest))
    from deskhelm_voice_host.launch import exec_voice_host

    return exec_voice_host(rest)


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
