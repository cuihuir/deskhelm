"""Run the protocol v2 web surface: ``python -m deskhelm_web [--socket PATH]``."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from deskhelm_protocol.paths import default_socket_path

from .http import default_web_token_path, load_web_token, normalize_web_host, normalize_web_port
from .server import WebSurface

DEFAULT_PORT = 8766


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deskhelm-web", description="Serve the DeskHelm page for the v2 Bridge.")
    parser.add_argument("--socket", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--allow-remote", action="store_true", help="bind beyond loopback; the token still guards it")
    parser.add_argument("--token-path", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        host = normalize_web_host(args.host, allow_remote=args.allow_remote)
        port = normalize_web_port(args.port)
    except ValueError as error:
        parser.error(str(error))
    socket_path = args.socket or default_socket_path()
    token = load_web_token(args.token_path or default_web_token_path())
    web = WebSurface(socket_path, host=host, port=port, access_token=token)
    web.start()
    print(f"deskhelm web reading {socket_path}", file=sys.stderr)
    print(f"deskhelm web listening on http://{web.host}:{web.port}", file=sys.stderr)
    print(f"open once to sign this browser in: http://{web.host}:{web.port}/?token={token}", file=sys.stderr)
    if web.host != "127.0.0.1":
        print("deskhelm web is bound beyond loopback and can show interaction content", file=sys.stderr)
    try:
        while web.serving:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        web.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
