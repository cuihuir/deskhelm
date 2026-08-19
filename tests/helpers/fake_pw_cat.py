#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import signal
import sys
import time


mode = sys.argv[1]
expected_target = sys.argv[2]
ready_path = sys.argv[3]
arguments = sys.argv[4:]


def option(name: str) -> str | None:
    try:
        return arguments[arguments.index(name) + 1]
    except (ValueError, IndexError):
        return None


def validate_common(direction: str) -> None:
    if direction not in arguments or "--raw" not in arguments:
        raise SystemExit(20)
    if option("--format") != "s16" or arguments[-1] != "-":
        raise SystemExit(21)
    target = option("--target")
    if expected_target == "__default__":
        if target is not None:
            raise SystemExit(22)
    elif target != expected_target:
        raise SystemExit(23)


def ready() -> None:
    if ready_path != "-":
        Path(ready_path).touch()


if mode.startswith("capture"):
    validate_common("--record")
    if option("--rate") != "16000" or option("--channels") != "1":
        raise SystemExit(24)
    if mode == "capture-failure":
        print("private capture failure", file=sys.stderr)
        raise SystemExit(7)
    if mode == "capture-overflow":
        sys.stdout.buffer.write(b"\x00\x00" * 4096)
        sys.stdout.buffer.flush()
        raise SystemExit(0)
    if mode == "capture-odd-frame":
        sys.stdout.buffer.write(b"odd")
        sys.stdout.buffer.flush()
        raise SystemExit(0)
    if mode == "capture-split-frame":
        sys.stdout.buffer.write(b"\x01\x00\x02")
        sys.stdout.buffer.flush()
        time.sleep(0.05)
        sys.stdout.buffer.write(b"\x00")
        sys.stdout.buffer.flush()
        raise SystemExit(0)
    sys.stdout.buffer.write(b"\x01\x00" * 320)
    sys.stdout.buffer.flush()
    if mode == "capture-ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    ready()
    if mode == "capture-ignore-term":
        time.sleep(30)
    if mode == "capture-hold":
        time.sleep(30)
    raise SystemExit(0)

if mode.startswith("playback"):
    validate_common("--playback")
    if option("--rate") != "24000" or option("--channels") != "1":
        raise SystemExit(25)
    data = sys.stdin.buffer.read()
    if not data or len(data) % 2:
        raise SystemExit(26)
    if mode == "playback-failure":
        print("private playback failure", file=sys.stderr)
        raise SystemExit(8)
    if mode == "playback-ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    ready()
    if mode == "playback-ignore-term":
        time.sleep(30)
    if mode == "playback-hold":
        time.sleep(30)
    raise SystemExit(0)

raise SystemExit(27)
