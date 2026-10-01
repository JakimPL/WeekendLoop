from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

from weekend_loop.claude_cli import CLAUDE_BINARY, SETPRIV_BINARY, TIMEOUT_BINARY
from weekend_loop.confinement import systemctl
from weekend_loop.github import GH_BINARY
from weekend_loop.models import Policy, Record, ScheduledCommand
from weekend_loop.preflight import SANDBOX_BINARIES
from weekend_loop.setup import messages
from weekend_loop.systemd_units import (
    TIMER_SUFFIX,
    UNIT_PREFIX,
    USER_UNIT_DIRECTORY,
    render_systemd_units,
    service_name,
    timer_name,
    unit_path_variable,
    write_units,
)
from weekend_loop.workbench import GIT_BINARY

LOGINCTL_BINARY: Final[str] = "loginctl"
UV_BINARY: Final[str] = "uv"
CLI_NAME: Final[str] = "weekend-loop"
USER_VARIABLE: Final[str] = "USER"
LINGER_PROPERTY: Final[str] = "Linger"
LINGER_ON: Final[str] = "yes"
ENABLED_STATE: Final[str] = "enabled"
ACTIVE_STATE: Final[str] = "active"
LOGINCTL_TIMEOUT_SECONDS: Final[int] = 30
REQUIRED_UNIT_BINARIES: Final[tuple[str, ...]] = (UV_BINARY, CLAUDE_BINARY)
SUPPORTING_UNIT_BINARIES: Final[tuple[str, ...]] = (
    GH_BINARY,
    GIT_BINARY,
    SETPRIV_BINARY,
    TIMEOUT_BINARY,
    *SANDBOX_BINARIES,
)


class UnitFailure(Record):
    command: str
    reason: str


def locate_binaries(names: tuple[str, ...]) -> dict[str, Path]:
    located = {name: shutil.which(name) for name in names}
    return {name: Path(location) for name, location in located.items() if location is not None}


def cli_binary() -> Path:
    located = shutil.which(CLI_NAME)
    if located is not None:
        return Path(located)
    return Path(sys.executable)


def missing_unit_binaries() -> list[str]:
    found = locate_binaries(REQUIRED_UNIT_BINARIES)
    return [name for name in REQUIRED_UNIT_BINARIES if name not in found]


def user_unit_directory() -> Path:
    return Path.home() / USER_UNIT_DIRECTORY


def rendered_units(policy: Policy, blocked_exit_status: int) -> dict[str, str]:
    binaries = locate_binaries((*REQUIRED_UNIT_BINARIES, *SUPPORTING_UNIT_BINARIES))
    return render_systemd_units(
        policy.schedule,
        policy.scheduled_repo_key,
        policy.workspace.root,
        cli_binary(),
        unit_path_variable(list(binaries.values())),
        blocked_exit_status,
        policy.resources,
    )


def scheduled_timers(policy: Policy) -> list[str]:
    return [timer_name(run) for run in policy.schedule.runs]


def installed_timers(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(
        path.name for path in directory.glob(f"{UNIT_PREFIX}-*{TIMER_SUFFIX}") if path.is_file()
    )


def failure_of(completed: subprocess.CompletedProcess[str]) -> UnitFailure | None:
    if completed.returncode == 0:
        return None
    lines = completed.stderr.strip().splitlines()
    return UnitFailure(
        command=" ".join(str(argument) for argument in completed.args),
        reason=lines[-1] if lines else messages.NO_ANSWER,
    )


def loginctl(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [LOGINCTL_BINARY, *arguments],
        env={"PATH": os.environ.get("PATH", "")},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=LOGINCTL_TIMEOUT_SECONDS,
    )


def user_manager_running() -> bool:
    return systemctl(["show-environment"]).returncode == 0


def install_units(directory: Path, units: dict[str, str]) -> UnitFailure | None:
    stale = [name for name in installed_timers(directory) if name not in units]
    if stale:
        systemctl(["disable", "--now", *stale])
        for name in stale:
            (directory / name).unlink()
    write_units(directory, units)
    return failure_of(systemctl(["daemon-reload"]))


def lingering_failure() -> UnitFailure | None:
    user = os.environ.get(USER_VARIABLE) or getpass.getuser()
    shown = loginctl(["show-user", user, "-p", LINGER_PROPERTY, "--value"])
    if shown.returncode == 0 and shown.stdout.strip() == LINGER_ON:
        return None
    return failure_of(loginctl(["enable-linger", user]))


def start_timers(timers: list[str]) -> UnitFailure | None:
    return failure_of(systemctl(["enable", "--now", *timers]))


def stop_timers(timers: list[str]) -> UnitFailure | None:
    if not timers:
        return None
    return failure_of(systemctl(["disable", "--now", *timers]))


def timers_enabled(timers: list[str]) -> bool:
    return all(systemctl(["is-enabled", timer]).stdout.strip() == ENABLED_STATE for timer in timers)


def commands_running() -> list[ScheduledCommand]:
    return [
        command
        for command in ScheduledCommand
        if systemctl(["is-active", service_name(command)]).stdout.strip() == ACTIVE_STATE
    ]
