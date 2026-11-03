"""Run the protocol v2 Bridge: ``python -m deskhelm_core [--socket PATH]``."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from deskhelm_protocol.paths import default_socket_path

from .server import BridgeServer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deskhelm-core", description="Run the protocol v2 Bridge.")
    parser.add_argument("--socket", type=Path, default=None, help="Unix socket path")
    args = parser.parse_args(argv)
    socket_path = args.socket or default_socket_path()
    if not socket_path.is_absolute():
        parser.error("--socket must be an absolute path")
    server = BridgeServer(socket_path)
    try:
        server.start()
    except RuntimeError as error:
        print(f"deskhelm bridge v2: {error}", file=sys.stderr)
        return 1
    print(f"deskhelm bridge v2 serving {socket_path} (instance {server.instance_id})", file=sys.stderr)
    try:
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
