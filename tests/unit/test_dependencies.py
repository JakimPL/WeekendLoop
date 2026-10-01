from __future__ import annotations

from typing import Final

from tests.unit.conftest import ELIGIBLE, build_assessment, build_task
from tests.unit.test_prefilter import build_issue
from weekend_loop.dependencies import (
    DependencyState,
    DependencyVerdict,
    dependencies_of,
    dependency_verdicts,
)
from weekend_loop.models import Effort, Issue, Risk, Task, TaskStatus, Verdict

TITLE: Final[str] = "Saved rules"
BODY: Final[str] = "## Business requirement\nPlayers lose their rules.\n"
MAX_DEPTH: Final[int] = 2


def task_on(number: int, depends_on: list[int], status: TaskStatus) -> Task:
    assessment = build_assessment(Verdict.EXECUTE, Effort.S, Risk.BEHAVIOUR, [], [])
    return build_task(
        number,
        TITLE,
        status,
        ELIGIBLE,
        assessment.model_copy(update={"depends_on": depends_on}),
    )


def open_issue(number: int, blocked_by: list[int]) -> Issue:
    return build_issue(number, TITLE, BODY, [], [], [], None).model_copy(
        update={"blocked_by": blocked_by}
    )


def verdicts(
    links: dict[int, list[int]], startable: set[int], max_depth: int
) -> dict[int, DependencyVerdict]:
    tasks = [task_on(number, parents, TaskStatus.ASSESSED) for number, parents in links.items()]
    issues = {number: open_issue(number, []) for number in links}
    return dependency_verdicts(tasks, issues, startable, max_depth)


def states(found: dict[int, DependencyVerdict]) -> dict[int, DependencyState]:
    return {number: verdict.state for number, verdict in found.items()}


def test_a_task_depends_on_what_github_links_and_what_the_assessor_found() -> None:
    task = task_on(17, [11, 17], TaskStatus.ASSESSED)
    assert dependencies_of(task, open_issue(17, [13])) == [11, 13]
    assert dependencies_of(task, None) == [11]


def test_a_dependency_that_closed_is_satisfied() -> None:
    tasks = [task_on(17, [13], TaskStatus.ASSESSED)]
    found = dependency_verdicts(tasks, {17: open_issue(17, [])}, {17}, MAX_DEPTH)
    assert found[17].state is DependencyState.SATISFIED


def test_a_child_stacks_on_a_parent_the_run_works_up_to_the_depth_allowed() -> None:
    found = verdicts({5: [], 11: [5], 13: [11], 17: [13]}, {5, 11, 13, 17}, MAX_DEPTH)
    assert states(found) == {
        5: DependencyState.SATISFIED,
        11: DependencyState.STACKED,
        13: DependencyState.STACKED,
        17: DependencyState.WAITING,
    }
    assert (found[11].parent, found[11].depth) == (5, 1)
    assert (found[13].parent, found[13].depth) == (11, 2)
    assert "worker.max_stack_depth (2)" in found[17].reason


def test_stacking_turned_off_keeps_every_child_waiting() -> None:
    found = verdicts({5: [], 11: [5]}, {5, 11}, 0)
    assert found[11].state is DependencyState.WAITING


def test_a_parent_the_run_leaves_alone_or_never_saw_keeps_its_child_waiting() -> None:
    unapproved = verdicts({5: [], 11: [5]}, {11}, MAX_DEPTH)
    assert unapproved[11].reason == "waits for #5, which this run leaves alone"
    tasks = [task_on(11, [5], TaskStatus.ASSESSED)]
    issues = {5: open_issue(5, []), 11: open_issue(11, [])}
    outside = dependency_verdicts(tasks, issues, {11}, MAX_DEPTH)
    assert outside[11].reason == "waits for #5, which is still open"


def test_a_parent_finished_earlier_in_the_run_carries_its_child() -> None:
    tasks = [task_on(5, [], TaskStatus.REVIEW), task_on(11, [5], TaskStatus.ASSESSED)]
    issues = {5: open_issue(5, []), 11: open_issue(11, [])}
    found = dependency_verdicts(tasks, issues, {11}, MAX_DEPTH)
    assert found[11].state is DependencyState.STACKED


def test_two_open_parents_or_a_second_child_wait_for_a_later_run() -> None:
    found = verdicts({5: [], 6: [], 11: [5, 6], 12: [5], 14: [5]}, {5, 6, 11, 12, 14}, MAX_DEPTH)
    assert found[11].reason == "builds on #5 and #6, which are all still open"
    assert found[12].state is DependencyState.STACKED
    assert found[14].reason == "#5 already carries #12 on top of it"


def test_a_cycle_and_whatever_hangs_below_it_wait() -> None:
    found = verdicts({5: [11], 11: [5], 13: [11]}, {5, 11, 13}, MAX_DEPTH)
    assert found[5].reason == "its dependencies run in a circle through #5, #11, #5"
    assert found[11].state is DependencyState.WAITING
    assert found[13].reason == "waits for #11, which waits itself"
