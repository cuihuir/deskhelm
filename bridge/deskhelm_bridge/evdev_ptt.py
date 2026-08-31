from __future__ import annotations

from pathlib import Path

from .ptt import RIGHT_ALT_KEY, RightAltPttController


def run_right_alt_listener(device_path: Path, controller: RightAltPttController) -> None:
    try:
        from evdev import InputDevice, ecodes
    except ModuleNotFoundError as error:
        raise RuntimeError("right-Alt PTT requires `uv sync --extra ptt`") from error
    device = InputDevice(str(device_path))
    try:
        for event in device.read_loop():
            if event.type != ecodes.EV_KEY or event.code != ecodes.KEY_RIGHTALT:
                continue
            if event.value == 1:
                controller.observe(RIGHT_ALT_KEY, True)
            elif event.value == 0:
                controller.observe(RIGHT_ALT_KEY, False)
    finally:
        device.close()
