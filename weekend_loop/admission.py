from __future__ import annotations

import os
import threading
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Final

from weekend_loop.models import Record, ResourcesPolicy

MEMINFO_PATH: Final[Path] = Path("/proc/meminfo")
MEMORY_PRESSURE_PATH: Final[Path] = Path("/proc/pressure/memory")
KIBIBYTES_PER_GIBIBYTE: Final[int] = 1024 * 1024
TOTAL_KEY: Final[str] = "MemTotal"
AVAILABLE_KEY: Final[str] = "MemAvailable"
PRESSURE_LINE_PREFIX: Final[str] = "some "
PRESSURE_FIELD: Final[str] = "avg10"
DEFAULT_MEMORY_POOL_SHARE: Final[float] = 0.5
MEMORY_PRESSURE_CEILING: Final[float] = 20.0
ADMISSION_POLL_SECONDS: Final[float] = 15.0
BASELINE_HOLDER: Final[int] = 0


class MemoryReading(Record):
    total_gb: float
    available_gb: float
    pressure_percent: float


class ReservationKind(StrEnum):
    WORKING = "working"
    GATING = "gating"


class Reservation(Record):
    holder: int
    kind: ReservationKind
    memory_gb: float


class AdmissionLimits(Record):
    pool_gb: float
    reserve_gb: float
    task_memory_gb: float
    gate_memory_gb: float
    gates_at_once: int


def meminfo_values(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        fields = rest.split()
        if fields and fields[0].isdigit():
            values[name.strip()] = int(fields[0])
    return values


def pressure_percent(text: str) -> float:
    for line in text.splitlines():
        if not line.startswith(PRESSURE_LINE_PREFIX):
            continue
        for field in line.split()[1:]:
            name, _, value = field.partition("=")
            if name == PRESSURE_FIELD:
                return float(value)
    return 0.0


def parse_memory(meminfo: str, pressure: str) -> MemoryReading:
    values = meminfo_values(meminfo)
    return MemoryReading(
        total_gb=values[TOTAL_KEY] / KIBIBYTES_PER_GIBIBYTE,
        available_gb=values[AVAILABLE_KEY] / KIBIBYTES_PER_GIBIBYTE,
        pressure_percent=pressure_percent(pressure),
    )


def read_memory() -> MemoryReading:
    pressure = MEMORY_PRESSURE_PATH.read_text() if MEMORY_PRESSURE_PATH.is_file() else ""
    return parse_memory(MEMINFO_PATH.read_text(), pressure)


def pool_size_gb(resources: ResourcesPolicy, reading: MemoryReading) -> float:
    if resources.memory_pool_gb is not None:
        return resources.memory_pool_gb
    return reading.total_gb * DEFAULT_MEMORY_POOL_SHARE


def admission_limits(resources: ResourcesPolicy, reading: MemoryReading) -> AdmissionLimits:
    return AdmissionLimits(
        pool_gb=pool_size_gb(resources, reading),
        reserve_gb=resources.memory_reserve_gb,
        task_memory_gb=resources.task_memory_gb,
        gate_memory_gb=resources.gate_memory_gb,
        gates_at_once=resources.gates_at_once,
    )


def tasks_at_once(limits: AdmissionLimits, parallel: int) -> int:
    beside_a_gate = int((limits.pool_gb - limits.gate_memory_gb) // limits.task_memory_gb) + 1
    return max(1, min(parallel, beside_a_gate))


def caps_exceeding_the_pool(limits: AdmissionLimits) -> list[str]:
    caps = {"task_memory_gb": limits.task_memory_gb, "gate_memory_gb": limits.gate_memory_gb}
    return [name for name, size in caps.items() if size > limits.pool_gb]


def reservation_for(limits: AdmissionLimits, holder: int, kind: ReservationKind) -> Reservation:
    memory = limits.task_memory_gb if kind is ReservationKind.WORKING else limits.gate_memory_gb
    return Reservation(holder=holder, kind=kind, memory_gb=memory)


def admits(
    limits: AdmissionLimits,
    held: list[Reservation],
    request: Reservation,
    reading: MemoryReading,
    gate_waiting: bool,
) -> bool:
    others = [reservation for reservation in held if reservation.holder != request.holder]
    if not others:
        return True
    if request.kind is ReservationKind.WORKING and gate_waiting:
        return False
    gates = sum(1 for reservation in others if reservation.kind is ReservationKind.GATING)
    if request.kind is ReservationKind.GATING and gates >= limits.gates_at_once:
        return False
    if sum(reservation.memory_gb for reservation in others) + request.memory_gb > limits.pool_gb:
        return False
    if reading.available_gb - request.memory_gb < limits.reserve_gb:
        return False
    return reading.pressure_percent <= MEMORY_PRESSURE_CEILING


def cpu_sets(cpus: list[int], cpus_per_task: int) -> list[list[int]]:
    ordered = sorted(cpus)
    whole = len(ordered) // cpus_per_task
    if whole == 0:
        return [ordered]
    return [ordered[index * cpus_per_task : (index + 1) * cpus_per_task] for index in range(whole)]


def available_cpus() -> list[int]:
    return sorted(os.sched_getaffinity(0))


class Admission:
    def __init__(
        self,
        limits: AdmissionLimits,
        memory: Callable[[], MemoryReading],
        sets: list[list[int]],
    ) -> None:
        self.limits = limits
        self.memory = memory
        self.sets = sets
        self.condition = threading.Condition()
        self.held: dict[int, Reservation] = {}
        self.waiting_gates: set[int] = set()
        self.cpus_by_holder: dict[int, list[int]] = {}
        self.setup_door = threading.Lock()

    def hold(
        self,
        holder: int,
        kind: ReservationKind,
        keep_waiting: Callable[[], bool],
        on_wait: Callable[[], None],
    ) -> bool:
        request = reservation_for(self.limits, holder, kind)
        with self.condition:
            if kind is ReservationKind.GATING:
                self.held.pop(holder, None)
                self.waiting_gates.add(holder)
                self.condition.notify_all()
            waited = False
            try:
                while not self.admitted(request):
                    if not keep_waiting():
                        return False
                    if not waited:
                        on_wait()
                        waited = True
                    self.condition.wait(timeout=ADMISSION_POLL_SECONDS)
                self.held[holder] = request
                self.assign_cpus(holder)
                return True
            finally:
                self.waiting_gates.discard(holder)
                self.condition.notify_all()

    def admitted(self, request: Reservation) -> bool:
        gate_waiting = bool(self.waiting_gates - {request.holder})
        return admits(self.limits, list(self.held.values()), request, self.memory(), gate_waiting)

    def assign_cpus(self, holder: int) -> None:
        if holder in self.cpus_by_holder or not self.sets:
            return
        taken = [tuple(cpus) for cpus in self.cpus_by_holder.values()]
        free = [cpus for cpus in self.sets if tuple(cpus) not in taken]
        busiest_last = free or sorted(self.sets, key=lambda cpus: taken.count(tuple(cpus)))
        self.cpus_by_holder[holder] = busiest_last[0]

    def cpus_of(self, holder: int) -> list[int]:
        with self.condition:
            return list(self.cpus_by_holder.get(holder, []))

    def release(self, holder: int) -> None:
        with self.condition:
            self.held.pop(holder, None)
            self.cpus_by_holder.pop(holder, None)
            self.condition.notify_all()
