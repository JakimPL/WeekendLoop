from __future__ import annotations

import os
import signal
import subprocess
from typing import Final

TERMINATION_GRACE_SECONDS: Final[float] = 20.0


def signal_group(process: subprocess.Popen[bytes], signal_number: int) -> None:
    try:
        os.killpg(process.pid, signal_number)
    except ProcessLookupError:
        return


def exited(process: subprocess.Popen[bytes], seconds: float) -> bool:
    try:
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        return False
    return True


def terminate(process: subprocess.Popen[bytes]) -> None:
    signal_group(process, signal.SIGTERM)
    if not exited(process, TERMINATION_GRACE_SECONDS):
        signal_group(process, signal.SIGKILL)
        process.wait()
    signal_group(process, signal.SIGKILL)
