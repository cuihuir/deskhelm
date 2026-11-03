"""Newline-delimited JSON framing shared by every protocol v2 role."""

from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = 2
MAX_FRAME_BYTES = 1024 * 1024


class WireError(ValueError):
    """A peer sent bytes that are not a valid frame."""


def encode_frame(frame: dict[str, Any]) -> bytes:
    data = json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_FRAME_BYTES:
        raise WireError("frame exceeds the maximum size")
    return data + b"\n"


def decode_frame(line: bytes) -> dict[str, Any]:
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WireError("frame must be one UTF-8 JSON object") from error
    if not isinstance(value, dict):
        raise WireError("frame must be one UTF-8 JSON object")
    if not isinstance(value.get("type"), str) or not value["type"]:
        raise WireError("frame needs a type")
    return value


class FrameDecoder:
    """Split a byte stream into frames, one JSON object per line."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        frames: list[dict[str, Any]] = []
        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                if len(self._buffer) > MAX_FRAME_BYTES:
                    raise WireError("frame exceeds the maximum size")
                return frames
            line = bytes(self._buffer[:index])
            del self._buffer[: index + 1]
            if line.endswith(b"\r"):
                line = line[:-1]
            if len(line) > MAX_FRAME_BYTES:
                raise WireError("frame exceeds the maximum size")
            if not line:
                raise WireError("frame must not be empty")
            frames.append(decode_frame(line))
