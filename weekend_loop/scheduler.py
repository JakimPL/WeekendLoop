from __future__ import annotations

from typing import Final

from weekend_loop.dependencies import stack_chains
from weekend_loop.models import Record, Task, TaskStatus
from weekend_loop.report import EFFORT_ORDER

PARENT_FAILED_TEMPLATE: Final[str] = (
    "its parent #{parent} did not reach review in this run, so it waits for a later one"
)


class WorkQueue(Record):
    tasks: list[Task]
    parents: dict[int, int]


class StartDecision(Record):
    task: Task | None
    dropped: dict[int, str]


def effort_rank(task: Task) -> int:
    if task.assessment is None:
        return len(EFFORT_ORDER)
    return EFFORT_ORDER[task.assessment.effort]


def queue_order(tasks: list[Task], parents: dict[int, int]) -> list[Task]:
    unblocking = set(parents.values())
    return sorted(
        tasks,
        key=lambda task: (
            task.issue_number not in unblocking,
            effort_rank(task),
            task.issue_number,
        ),
    )


def next_start(
    queue: list[Task],
    statuses: dict[int, TaskStatus],
    parents: dict[int, int],
    pending: set[int],
) -> StartDecision:
    dropped: dict[int, str] = {}
    for task in queue:
        parent = parents.get(task.issue_number)
        if parent is None or statuses.get(parent) is TaskStatus.REVIEW:
            return StartDecision(task=task, dropped=dropped)
        if parent in pending and parent not in dropped:
            continue
        dropped[task.issue_number] = PARENT_FAILED_TEMPLATE.format(parent=parent)
    return StartDecision(task=None, dropped=dropped)


def stack_containing(number: int, parents: dict[int, int]) -> list[int]:
    return next((chain for chain in stack_chains(parents) if number in chain), [number])


def same_stack_pairs(parents: dict[int, int]) -> set[frozenset[int]]:
    return {
        frozenset((first, second))
        for chain in stack_chains(parents)
        for first in chain
        for second in chain
        if first != second
    }
