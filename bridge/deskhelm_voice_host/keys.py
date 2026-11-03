"""Report right-Alt edges to the voice host, one ``down`` or ``up`` per line.

``python -m deskhelm_voice_host.keys --device PATH`` runs in the environment
that holds ``evdev`` (``uv sync --extra ptt``), so the voice runtime does not
need it. It reads keyd's virtual keyboard without EVIOCGRAB, which would hide
every key from the desktop, and one listener at a time holds the same
``ptt.lock`` as the v1 listener (ADR 0039).
"""

from __future__ import annotations

import argparse
import fcntl
from pathlib import Path
import sys

from deskhelm_protocol.paths import runtime_directory

DEFAULT_DEVICE = Path("/dev/input/deskhelm-ptt-kbd")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deskhelm-voice-keys")
    parser.add_argument("--device", type=Path, default=DEFAULT_DEVICE)
    parser.add_argument("--check", action="store_true", help="open the device and the lock, then exit")
    args = parser.parse_args(argv)
    try:
        from evdev import InputDevice, ecodes
    except ModuleNotFoundError:
        print("deskhelm voice: right-Alt PTT requires `uv sync --extra ptt`", file=sys.stderr)
        return 2
    directory = runtime_directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (directory / "ptt.lock").open("a")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("deskhelm voice: another right-Alt listener owns the PTT key", file=sys.stderr)
            return 1
        try:
            device = InputDevice(str(args.device))
        except OSError as error:
            print(f"deskhelm voice: cannot read {args.device}: {error.strerror or error}", file=sys.stderr)
            return 1
        try:
            if args.check:
                print("ok", flush=True)
                return 0
            for event in device.read_loop():
                if event.type != ecodes.EV_KEY or event.code != ecodes.KEY_RIGHTALT:
                    continue
                if event.value == 1:
                    print("down", flush=True)
                elif event.value == 0:
                    print("up", flush=True)
        except (BrokenPipeError, KeyboardInterrupt):
            return 0
        finally:
            device.close()
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
