import shlex
import sys
from pathlib import Path
from typing import Final

from weekend_loop.local_github.paths import board_root, wrapper_path

WRAPPER_MARKER: Final[str] = "# weekend-loop local board"
WRAPPER_MODE: Final[int] = 0o755
MARKER_SEARCH_BYTES: Final[int] = 256
MODULE: Final[str] = "weekend_loop.local_github"


def wrapper_script(root: Path, interpreter: str) -> str:
    command = shlex.join([interpreter, "-I", "-m", MODULE, "--root", str(root)])
    return f'#!/bin/sh\n{WRAPPER_MARKER}\nexec {command} "$@"\n'


def install_wrapper(state_directory: Path) -> Path:
    path = wrapper_path(state_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(wrapper_script(board_root(state_directory), sys.executable))
    path.chmod(WRAPPER_MODE)
    return path


def is_local_board_wrapper(path: Path) -> bool:
    try:
        with path.open("rb") as script:
            head = script.read(MARKER_SEARCH_BYTES)
    except OSError:
        return False
    return WRAPPER_MARKER.encode() in head
