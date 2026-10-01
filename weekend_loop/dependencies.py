from __future__ import annotations

from enum import StrEnum
from typing import Final

from weekend_loop.models import Issue, Record, Task, TaskStatus

STACK_BASE_STATUSES: Final[tuple[TaskStatus, ...]] = (TaskStatus.REVIEW,)
OUTSIDE_RUN_TEMPLATE: Final[str] = "waits for #{parent}, which is still open"
NOT_WORKED_TEMPLATE: Final[str] = "waits for #{parent}, which this run leaves alone"
TWO_PARENTS_TEMPLATE: Final[str] = "builds on {parents}, which are all still open"
SECOND_CHILD_TEMPLATE: Final[str] = "#{parent} already carries #{sibling} on top of it"
TOO_DEEP_TEMPLATE: Final[str] = (
    "builds on #{parent}, one layer more than worker.max_stack_depth ({depth}) allows"
)
CYCLE_TEMPLATE: Final[str] = "its dependencies run in a circle through {members}"
PARENT_WAITS_TEMPLATE: Final[str] = "waits for #{parent}, which waits itself"
STACKED_TEMPLATE: Final[str] = "builds on #{parent}, which this run works first"
SATISFIED_REASON: Final[str] = "everything it builds on has merged"


class DependencyState(StrEnum):
    SATISFIED = "satisfied"
    STACKED = "stacked"
    WAITING = "waiting"


class DependencyVerdict(Record):
    state: DependencyState
    parent: int | None
    depth: int
    reason: str


def dependencies_of(task: Task, issue: Issue | None) -> list[int]:
    declared = issue.blocked_by if issue is not None else []
    stored = task.spec_signals.blocked_by if task.spec_signals is not None else []
    inferred = task.assessment.depends_on if task.assessment is not None else []
    return sorted({*declared, *stored, *inferred} - {task.issue_number})


def satisfied() -> DependencyVerdict:
    return DependencyVerdict(
        state=DependencyState.SATISFIED, parent=None, depth=0, reason=SATISFIED_REASON
    )


def waiting(parent: int | None, reason: str) -> DependencyVerdict:
    return DependencyVerdict(state=DependencyState.WAITING, parent=parent, depth=0, reason=reason)


def hashed(numbers: list[int]) -> str:
    return " and ".join(f"#{number}" for number in numbers)


class DependencyGraph:
    def __init__(
        self,
        tasks: list[Task],
        issues: dict[int, Issue],
        startable: set[int],
        max_depth: int,
    ) -> None:
        self.tasks = {task.issue_number: task for task in tasks}
        self.issues = issues
        self.startable = startable
        self.max_depth = max_depth
        self.verdicts: dict[int, DependencyVerdict] = {}
        self.children: dict[int, int] = {}

    def open_parents(self, number: int) -> list[int]:
        task = self.tasks[number]
        parents = dependencies_of(task, self.issues.get(number))
        return [parent for parent in parents if parent in self.issues]

    def cycle_through(self, number: int) -> list[int]:
        chain = [number]
        while True:
            parents = self.open_parents(chain[-1])
            if len(parents) != 1 or parents[0] not in self.tasks:
                return []
            if parents[0] == number:
                return [*chain, number]
            if parents[0] in chain:
                return []
            chain.append(parents[0])

    def verdict(self, number: int) -> DependencyVerdict:
        if number not in self.verdicts:
            self.verdicts[number] = self.decide(number)
        return self.verdicts[number]

    def decide(self, number: int) -> DependencyVerdict:
        parents = self.open_parents(number)
        if not parents:
            return satisfied()
        cycle = self.cycle_through(number)
        if cycle:
            return waiting(None, CYCLE_TEMPLATE.format(members=", ".join(f"#{n}" for n in cycle)))
        if len(parents) > 1:
            return waiting(None, TWO_PARENTS_TEMPLATE.format(parents=hashed(parents)))
        parent = parents[0]
        task = self.tasks.get(parent)
        if task is None:
            return waiting(parent, OUTSIDE_RUN_TEMPLATE.format(parent=parent))
        if parent not in self.startable and task.status not in STACK_BASE_STATUSES:
            return waiting(parent, NOT_WORKED_TEMPLATE.format(parent=parent))
        above = self.verdict(parent)
        if above.state is DependencyState.WAITING:
            return waiting(parent, PARENT_WAITS_TEMPLATE.format(parent=parent))
        if above.depth + 1 > self.max_depth:
            return waiting(parent, TOO_DEEP_TEMPLATE.format(parent=parent, depth=self.max_depth))
        sibling = self.children.setdefault(parent, number)
        if sibling != number:
            return waiting(parent, SECOND_CHILD_TEMPLATE.format(parent=parent, sibling=sibling))
        return DependencyVerdict(
            state=DependencyState.STACKED,
            parent=parent,
            depth=above.depth + 1,
            reason=STACKED_TEMPLATE.format(parent=parent),
        )


def dependency_verdicts(
    tasks: list[Task],
    issues: dict[int, Issue],
    startable: set[int],
    max_depth: int,
) -> dict[int, DependencyVerdict]:
    graph = DependencyGraph(tasks, issues, startable, max_depth)
    return {number: graph.verdict(number) for number in sorted(graph.tasks)}
