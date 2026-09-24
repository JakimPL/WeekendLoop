from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, TextIO

LOCK_FILENAME: Final[str] = "weekend-loop.lock"


class RunLockHeldError(RuntimeError): ...


def lock_path(state_directory: Path) -> Path:
    return state_directory / LOCK_FILENAME


def record_holder(handle: TextIO) -> None:
    handle.truncate(0)
    handle.write(f"{os.getpid()}\n")
    handle.flush()


@contextmanager
def run_lock(state_directory: Path) -> Iterator[Path]:
    path = lock_path(state_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Append mode opens without truncating, so a refused attempt leaves the holder's pid intact.
    handle = path.open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        holder = path.read_text().strip()
        raise RunLockHeldError(f"another weekend-loop run (pid {holder}) holds {path}") from error
    try:
        record_holder(handle)
        yield path
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
