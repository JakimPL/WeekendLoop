from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Final

from weekend_loop.fences import RUNTIME_DIRECTORY_VARIABLE, user_runtime_directory
from weekend_loop.models import Record, ResourcesPolicy

SYSTEMD_RUN_BINARY: Final[str] = "systemd-run"
SYSTEMCTL_BINARY: Final[str] = "systemctl"
CHOOM_BINARY: Final[str] = "choom"
TASKSET_BINARY: Final[str] = "taskset"
ENV_BINARY: Final[str] = "env"
TASKS_SLICE: Final[str] = "weekend-loop-tasks.slice"
UNIT_PREFIX: Final[str] = "wl"
SCOPE_SUFFIX: Final[str] = ".scope"
LEFTOVER_PATTERN: Final[str] = f"{UNIT_PREFIX}-*{SCOPE_SUFFIX}"
BUS_ADDRESS_VARIABLE: Final[str] = "DBUS_SESSION_BUS_ADDRESS"
XDIST_WORKERS_VARIABLE: Final[str] = "PYTEST_XDIST_AUTO_NUM_WORKERS"
OOM_SCORE_ADJUSTMENT: Final[int] = 800
TASKS_MAX: Final[int] = 4096
RUNTIME_MARGIN_SECONDS: Final[int] = 300
BYTES_PER_GIBIBYTE: Final[int] = 1024**3
CGROUP_ROOT: Final[Path] = Path("/sys/fs/cgroup")
MEMORY_PEAK_FILENAME: Final[str] = "memory.peak"
CONTROL_GROUP_PROPERTY: Final[str] = "ControlGroup"
RESULT_PROPERTY: Final[str] = "Result"
OOM_KILL_RESULT: Final[str] = "oom-kill"
SYSTEMCTL_TIMEOUT_SECONDS: Final[int] = 30
UNIT_SUFFIX_CHARACTERS: Final[int] = 8
UNIT_CHARACTER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_.-]")


class OomPolicy(StrEnum):
    KILL = "kill"
    CONTINUE = "continue"


class Step(StrEnum):
    WORKER = "worker"
    SETUP = "setup"
    GATE = "gate"
    ACCEPTANCE = "acceptance"


class Confinement(Record):
    unit_prefix: str
    memory_gb: float
    cpus: list[int]
    oom_policy: OomPolicy
    runtime_seconds: int
    scoped: bool


def unit_prefix(run_id: str, issue_number: int, step: Step) -> str:
    return UNIT_CHARACTER_PATTERN.sub("_", f"{UNIT_PREFIX}-{run_id}-{issue_number}-{step.value}")


def unit_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:UNIT_SUFFIX_CHARACTERS]}"


def memory_bytes(memory_gb: float) -> int:
    return int(memory_gb * BYTES_PER_GIBIBYTE)


def scope_prefix(confinement: Confinement, unit: str) -> list[str]:
    properties = {
        "MemoryMax": str(memory_bytes(confinement.memory_gb)),
        "MemorySwapMax": "0",
        "TasksMax": str(TASKS_MAX),
        "RuntimeMaxSec": str(confinement.runtime_seconds),
        "OOMPolicy": confinement.oom_policy.value,
    }
    return [
        SYSTEMD_RUN_BINARY,
        "--user",
        "--scope",
        "--quiet",
        f"--slice={TASKS_SLICE}",
        f"--unit={unit}",
        *[argument for name, value in properties.items() for argument in ("-p", f"{name}={value}")],
        "--",
        ENV_BINARY,
        "-u",
        RUNTIME_DIRECTORY_VARIABLE,
        "-u",
        BUS_ADDRESS_VARIABLE,
    ]


def confined(command: list[str], confinement: Confinement, unit: str) -> list[str]:
    scope = scope_prefix(confinement, unit) if confinement.scoped else []
    cpus = [TASKSET_BINARY, "-c", ",".join(str(cpu) for cpu in confinement.cpus)]
    pinned = cpus if confinement.cpus else []
    return [*scope, CHOOM_BINARY, "-n", str(OOM_SCORE_ADJUSTMENT), "--", *pinned, *command]


def bus_environment() -> dict[str, str]:
    return {RUNTIME_DIRECTORY_VARIABLE: str(user_runtime_directory())}


def confined_environment(environment: dict[str, str], confinement: Confinement) -> dict[str, str]:
    return {**environment, **bus_environment()} if confinement.scoped else environment


def fan_out_environment(resources: ResourcesPolicy) -> dict[str, str]:
    if resources.cpus_per_task is None:
        return {}
    return {XDIST_WORKERS_VARIABLE: str(resources.cpus_per_task)}


def systemctl(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    environment = {"PATH": os.environ.get("PATH", ""), **bus_environment()}
    return subprocess.run(
        [SYSTEMCTL_BINARY, "--user", *arguments],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=SYSTEMCTL_TIMEOUT_SECONDS,
    )


def scope_property(unit: str, name: str) -> str:
    return systemctl(["show", "-p", name, "--value", f"{unit}{SCOPE_SUFFIX}"]).stdout.strip()


def scope_cgroup(unit: str) -> Path | None:
    group = scope_property(unit, CONTROL_GROUP_PROPERTY)
    return CGROUP_ROOT / group.lstrip("/") if group else None


def read_peak_gb(cgroup: Path) -> float | None:
    try:
        return int((cgroup / MEMORY_PEAK_FILENAME).read_text()) / BYTES_PER_GIBIBYTE
    except (OSError, ValueError):
        return None


class PeakWatch:
    def __init__(self, unit: str | None) -> None:
        self.unit = unit
        self.cgroup: Path | None = None
        self.peak_gb: float | None = None

    def observe(self) -> None:
        if self.unit is None:
            return
        if self.cgroup is None:
            self.cgroup = scope_cgroup(self.unit)
        reading = read_peak_gb(self.cgroup) if self.cgroup is not None else None
        if reading is not None:
            self.peak_gb = max(self.peak_gb or 0.0, reading)


def stopped_at_memory_cap(unit: str) -> bool:
    capped = scope_property(unit, RESULT_PROPERTY) == OOM_KILL_RESULT
    systemctl(["reset-failed", f"{unit}{SCOPE_SUFFIX}"])
    return capped


def stop_scope(unit: str) -> None:
    systemctl(["stop", f"{unit}{SCOPE_SUFFIX}"])


def stop_leftover_scopes() -> None:
    systemctl(["stop", LEFTOVER_PATTERN])
    systemctl(["reset-failed", LEFTOVER_PATTERN])


def scopes_available() -> bool:
    if shutil.which(SYSTEMD_RUN_BINARY) is None:
        return False
    probe = unit_name(f"{UNIT_PREFIX}-probe")
    completed = subprocess.run(
        [
            SYSTEMD_RUN_BINARY,
            "--user",
            "--scope",
            "--quiet",
            f"--slice={TASKS_SLICE}",
            f"--unit={probe}",
            "--",
            "true",
        ],
        env={"PATH": os.environ.get("PATH", ""), **bus_environment()},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=SYSTEMCTL_TIMEOUT_SECONDS,
    )
    return completed.returncode == 0
