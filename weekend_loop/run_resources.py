from __future__ import annotations

from typing import Final

from weekend_loop.admission import (
    Admission,
    admission_limits,
    available_cpus,
    cpu_sets,
    read_memory,
)
from weekend_loop.commands import DEFAULT_COMMAND_TIMEOUT_SECONDS
from weekend_loop.confinement import (
    RUNTIME_MARGIN_SECONDS,
    Confinement,
    OomPolicy,
    Step,
    scopes_available,
    stop_leftover_scopes,
    unit_prefix,
)
from weekend_loop.models import Policy

SECONDS_PER_MINUTE: Final[int] = 60
TASK_MEMORY_STEPS: Final[frozenset[Step]] = frozenset({Step.WORKER, Step.SETUP})


class RunResources:
    def __init__(
        self, admission: Admission, scoped: bool, run_id: str, worker_timeout_seconds: int
    ) -> None:
        self.admission = admission
        self.scoped = scoped
        self.run_id = run_id
        self.worker_timeout_seconds = worker_timeout_seconds

    def confinement(self, holder: int, step: Step) -> Confinement:
        limits = self.admission.limits
        task_step = step in TASK_MEMORY_STEPS
        timeout = (
            self.worker_timeout_seconds if step is Step.WORKER else DEFAULT_COMMAND_TIMEOUT_SECONDS
        )
        return Confinement(
            unit_prefix=unit_prefix(self.run_id, holder, step),
            memory_gb=limits.task_memory_gb if task_step else limits.gate_memory_gb,
            cpus=self.admission.cpus_of(holder),
            oom_policy=OomPolicy.CONTINUE if step is Step.WORKER else OomPolicy.KILL,
            runtime_seconds=timeout + RUNTIME_MARGIN_SECONDS,
            scoped=self.scoped,
        )


def open_run_resources(policy: Policy, run_id: str) -> RunResources:
    resources = policy.resources
    sets = (
        cpu_sets(available_cpus(), resources.cpus_per_task)
        if resources.cpus_per_task is not None
        else []
    )
    scoped = scopes_available()
    if scoped:
        stop_leftover_scopes()
    return RunResources(
        Admission(admission_limits(resources, read_memory()), read_memory, sets),
        scoped,
        run_id,
        policy.worker.timeout_minutes * SECONDS_PER_MINUTE,
    )
