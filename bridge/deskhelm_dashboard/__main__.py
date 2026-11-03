"""Run the protocol v2 dashboard: ``python -m deskhelm_dashboard [--socket PATH]``."""

from __future__ import annotations

import argparse
from pathlib import Path

from deskhelm_protocol.paths import default_socket_path

from .app import run_dashboard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deskhelm-dashboard", description="Watch and steer the v2 Bridge sessions.")
    parser.add_argument("--socket", type=Path, default=None)
    args = parser.parse_args(argv)
    return run_dashboard(args.socket or default_socket_path())


if __name__ == "__main__":
    raise SystemExit(main())
