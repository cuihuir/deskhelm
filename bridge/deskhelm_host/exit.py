"""End a host the way the signal that stopped it would have."""

from __future__ import annotations

import os
import signal


def stop_by_signal(signal_number: int) -> int:
    """Re-raise a stop signal after cleanup, so a shell or a service manager
    sees the process stopped, not failed. Returns only if the signal does not
    end the process."""
    signal.signal(signal_number, signal.SIG_DFL)
    os.kill(os.getpid(), signal_number)
    return 128 + signal_number
