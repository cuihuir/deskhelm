from __future__ import annotations

import fcntl
from pathlib import Path

from .paths import deskhelm_runtime_directory
from .ptt import RIGHT_ALT_KEY, RightAltPttController


def run_right_alt_listener(device_path: Path, controller: RightAltPttController) -> None:
    try:
        from evdev import InputDevice, ecodes
    except ModuleNotFoundError as error:
        raise RuntimeError("right-Alt PTT requires `uv sync --extra ptt`") from error
    device = InputDevice(str(device_path))
    lock_handle = None
    try:
        # Do not grab. This node is keyd's virtual keyboard, and EVIOCGRAB
        # would hide every key from the desktop. ADR 0039.
        lock_handle = _acquire_ptt_lock()
        for event in device.read_loop():
            if event.type != ecodes.EV_KEY or event.code != ecodes.KEY_RIGHTALT:
                continue
            if event.value == 1:
                controller.observe(RIGHT_ALT_KEY, True)
            elif event.value == 0:
                controller.observe(RIGHT_ALT_KEY, False)
    finally:
        if lock_handle is not None:
            _release_ptt_lock(lock_handle)
        device.close()


def _acquire_ptt_lock():
    directory = deskhelm_runtime_directory()
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "ptt.lock").open("a")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise RuntimeError("right-Alt PTT input device is already owned") from error
    return handle


def _release_ptt_lock(handle) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    handle.close()
