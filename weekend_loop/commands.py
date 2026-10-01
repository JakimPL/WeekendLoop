from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Final

from weekend_loop.confinement import (
    Confinement,
    PeakWatch,
    confined,
    confined_environment,
    stop_scope,
    stopped_at_memory_cap,
    unit_name,
)
from weekend_loop.models import CommandResult
from weekend_loop.processes import exited, terminate

OUTPUT_TAIL_CHARACTERS: Final[int] = 2000
TIMEOUT_EXIT_CODE: Final[int] = 124
DEFAULT_COMMAND_TIMEOUT_SECONDS: Final[int] = 1800
POLL_INTERVAL_SECONDS: Final[float] = 2.0
INHERITED_ENVIRONMENT_KEYS: Final[tuple[str, ...]] = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")


def command_environment(extra: dict[str, str]) -> dict[str, str]:
    environment = {key: os.environ[key] for key in INHERITED_ENVIRONMENT_KEYS if key in os.environ}
    environment.update(extra)
    return environment


def output_tail(text: str) -> str:
    return text.strip()[-OUTPUT_TAIL_CHARACTERS:]


def run_command(
    command: str,
    working_directory: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    confinement: Confinement | None,
) -> CommandResult:
    started_at = time.monotonic()
    arguments = shlex.split(command)
    scope: str | None = None
    if confinement is not None:
        unit = unit_name(confinement.unit_prefix)
        arguments = confined(arguments, confinement, unit)
        environment = confined_environment(environment, confinement)
        scope = unit if confinement.scoped else None
    watch = PeakWatch(scope)
    with tempfile.TemporaryFile() as output:
        with subprocess.Popen(
            arguments,
            cwd=working_directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process:
            timed_out = waited_out(process, watch, timeout_seconds)
        output.seek(0)
        text = output.read().decode(errors="replace")
    if timed_out:
        if scope is not None:
            stop_scope(scope)
        return CommandResult(
            command=command,
            exit_code=TIMEOUT_EXIT_CODE,
            duration_seconds=time.monotonic() - started_at,
            output_tail=f"command exceeded {timeout_seconds}s",
            peak_memory_gb=watch.peak_gb,
        )
    return CommandResult(
        command=command,
        exit_code=process.returncode,
        duration_seconds=time.monotonic() - started_at,
        output_tail=output_tail(text),
        peak_memory_gb=watch.peak_gb,
        stopped_at_memory_cap=(
            scope is not None and process.returncode != 0 and stopped_at_memory_cap(scope)
        ),
    )


def waited_out(process: subprocess.Popen[bytes], watch: PeakWatch, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while not exited(process, POLL_INTERVAL_SECONDS):
        watch.observe()
        if time.monotonic() >= deadline:
            terminate(process)
            return True
    watch.observe()
    return False
