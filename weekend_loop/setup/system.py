from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path
from typing import Final

from weekend_loop.commands import command_environment, run_command
from weekend_loop.models import CheckOutcome, Record, Workspace
from weekend_loop.preflight import (
    NPM_BINARY,
    SOCKET_FILTER_PACKAGE,
    check_sandbox_starts,
    global_node_modules,
)
from weekend_loop.resources import ResourceKind, packaged_text
from weekend_loop.setup import messages
from weekend_loop.setup.outcomes import StepOutcome, done, failed, todo

APT_BINARY: Final[str] = "apt-get"
SANDBOX_PACKAGES: Final[tuple[tuple[str, str], ...]] = (("bwrap", "bubblewrap"), ("socat", "socat"))
APPARMOR_RESOURCE: Final[str] = "bwrap.apparmor"
APPARMOR_TARGET: Final[str] = "/etc/apparmor.d/bwrap"
SOCKET_FILTER_INSTALL: Final[list[str]] = [NPM_BINARY, "install", "-g", str(SOCKET_FILTER_PACKAGE)]
NPM_TIMEOUT_SECONDS: Final[int] = 600


class SystemSteps(Record):
    outcomes: list[StepOutcome]
    root_commands: list[str]


def missing_packages() -> list[str]:
    return [package for binary, package in SANDBOX_PACKAGES if shutil.which(binary) is None]


def package_command(packages: list[str]) -> str:
    if shutil.which(APT_BINARY) is not None:
        return f"sudo {APT_BINARY} install {' '.join(packages)}"
    return f"sudo <your package manager> install {' '.join(packages)}"


def apparmor_commands(workspace: Workspace) -> list[str]:
    profile = workspace.apparmor_profile_path
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(packaged_text(ResourceKind.SYSTEM, APPARMOR_RESOURCE))
    return [
        f"sudo install -m 644 {shlex.quote(str(profile))} {APPARMOR_TARGET}",
        f"sudo apparmor_parser -r {APPARMOR_TARGET}",
    ]


def sandbox_steps(workspace: Workspace) -> SystemSteps:
    packages = missing_packages()
    if packages:
        commands = [package_command(packages)]
    elif check_sandbox_starts(False).outcome is not CheckOutcome.PASSED:
        commands = apparmor_commands(workspace)
    else:
        commands = []
    outcome = (
        todo(messages.SANDBOX, messages.SANDBOX_NEEDS_ROOT)
        if commands
        else done(messages.SANDBOX, messages.SANDBOX_READY)
    )
    return SystemSteps(outcomes=[outcome], root_commands=commands)


def socket_filter_steps(install: bool) -> SystemSteps:
    modules = global_node_modules()
    if modules is not None and (modules / SOCKET_FILTER_PACKAGE).is_dir():
        return SystemSteps(
            outcomes=[done(messages.SOCKET_FILTER, messages.SOCKET_FILTER_READY)], root_commands=[]
        )
    if modules is None:
        return SystemSteps(
            outcomes=[todo(messages.SOCKET_FILTER, messages.SOCKET_FILTER_NEEDS_NPM)],
            root_commands=[],
        )
    if not install:
        command = shlex.join(SOCKET_FILTER_INSTALL)
        outcome = todo(
            messages.SOCKET_FILTER, messages.SOCKET_FILTER_MISSING.format(command=command)
        )
        return SystemSteps(outcomes=[outcome], root_commands=[])
    if not os.access(modules, os.W_OK):
        return SystemSteps(
            outcomes=[todo(messages.SOCKET_FILTER, messages.SANDBOX_NEEDS_ROOT)],
            root_commands=[f"sudo {shlex.join(SOCKET_FILTER_INSTALL)}"],
        )
    result = run_command(
        shlex.join(SOCKET_FILTER_INSTALL),
        Path.cwd(),
        command_environment({}),
        NPM_TIMEOUT_SECONDS,
        None,
    )
    if result.exit_code != 0:
        lines = result.output_tail.splitlines()
        reason = lines[-1] if lines else messages.NO_ANSWER
        outcome = failed(
            messages.SOCKET_FILTER, messages.SOCKET_FILTER_FAILED.format(reason=reason)
        )
        return SystemSteps(outcomes=[outcome], root_commands=[])
    return SystemSteps(
        outcomes=[done(messages.SOCKET_FILTER, messages.SOCKET_FILTER_INSTALLED)], root_commands=[]
    )
