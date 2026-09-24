from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Final

from weekend_loop.models import CommandResult

OUTPUT_TAIL_CHARACTERS: Final[int] = 2000
TIMEOUT_EXIT_CODE: Final[int] = 124
DEFAULT_COMMAND_TIMEOUT_SECONDS: Final[int] = 1800
INHERITED_ENVIRONMENT_KEYS: Final[tuple[str, ...]] = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")


def command_environment(extra: dict[str, str]) -> dict[str, str]:
    environment = {key: os.environ[key] for key in INHERITED_ENVIRONMENT_KEYS if key in os.environ}
    environment.update(extra)
    return environment


def output_tail(text: str) -> str:
    return text.strip()[-OUTPUT_TAIL_CHARACTERS:]


def run_command(
    command: str, working_directory: Path, environment: dict[str, str], timeout_seconds: int
) -> CommandResult:
    started_at = time.monotonic()
    try:
        completed = subprocess.run(
            shlex.split(command),
            cwd=working_directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            command=command,
            exit_code=TIMEOUT_EXIT_CODE,
            duration_seconds=time.monotonic() - started_at,
            output_tail=f"command exceeded {timeout_seconds}s",
        )
    return CommandResult(
        command=command,
        exit_code=completed.returncode,
        duration_seconds=time.monotonic() - started_at,
        output_tail=output_tail(f"{completed.stdout}\n{completed.stderr}"),
    )
