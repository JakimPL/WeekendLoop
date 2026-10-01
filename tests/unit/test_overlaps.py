from __future__ import annotations

import pytest

from tests.unit.conftest import ELIGIBLE, build_assessment, build_task
from weekend_loop.models import Effort, Overlap, Risk, Task, TaskStatus, Verdict
from weekend_loop.overlaps import (
    hand_written,
    normalised_path,
    other_paths,
    overlap,
    overlaps_found,
    pairs_sharing_files,
)

NO_SHARED_PATHS: list[str] = []


def task_touching(issue_number: int, paths: list[str]) -> Task:
    assessment = build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []).model_copy(
        update={"touched_paths": paths}
    )
    return build_task(
        issue_number, f"issue {issue_number}", TaskStatus.ASSESSED, ELIGIBLE, assessment
    )


def test_a_directory_covers_the_files_inside_it() -> None:
    assert overlap("src", "src/app.py")
    assert overlap("src/app.py", "src")
    assert not overlap("source.py", "source")
    assert overlap("", "anything/at/all.py")


@pytest.mark.parametrize(
    ("changed", "pattern"),
    [
        ("docs/generated/table.md", "docs/generated/**"),
        ("docs/generated/table.md", "docs/generated"),
        ("docs", "docs/generated/**"),
        ("notes/CHANGELOG.md", "**/CHANGELOG.md"),
    ],
)
def test_a_shared_pattern_covers_what_a_path_would(changed: str, pattern: str) -> None:
    assert hand_written([changed, "src/app.py"], [pattern]) == {"src/app.py"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/app.py", "src/app.py"),
        ("src/app.py (new)", "src/app.py"),
        ("  ./src/app.py  ", "src/app.py"),
        ("/src//app.py/", "src/app.py"),
        ("`src/app.py` (new)", "src/app.py"),
        (".", ""),
    ],
)
def test_a_touched_path_is_read_the_way_the_assessor_wrote_it(raw: str, expected: str) -> None:
    assert normalised_path(raw) == expected


def test_two_branches_that_changed_the_same_file_pair_up_unless_the_file_is_shared() -> None:
    changed = {1: ["a.py", "CHANGELOG.md"], 2: ["a.py", "CHANGELOG.md"], 3: ["b.py"]}
    assert pairs_sharing_files(changed, ["CHANGELOG.md"], set()) == {(1, 2): ["a.py"]}
    assert (
        pairs_sharing_files({1: ["CHANGELOG.md"], 2: ["CHANGELOG.md"]}, ["CHANGELOG.md"], set())
        == {}
    )


def test_branches_of_one_stack_are_never_paired() -> None:
    changed = {1: ["a.py"], 2: ["a.py"], 3: ["a.py"]}
    assert pairs_sharing_files(changed, NO_SHARED_PATHS, {frozenset((1, 2))}) == {
        (1, 3): ["a.py"],
        (2, 3): ["a.py"],
    }


def test_a_conflict_asks_for_care_and_a_clean_merge_only_for_a_note() -> None:
    sharing = {(1, 2): ["a.py", "b.py"], (1, 3): ["c.py"]}
    found = overlaps_found(sharing, {(1, 2): ["a.py", "CHANGELOG.md"], (1, 3): []})
    assert found[2] == [Overlap(issue_number=1, paths=["a.py"], merges_cleanly=False)]
    assert found[3] == [Overlap(issue_number=1, paths=["c.py"], merges_cleanly=True)]
    assert found[1] == [
        Overlap(issue_number=2, paths=["a.py"], merges_cleanly=False),
        Overlap(issue_number=3, paths=["c.py"], merges_cleanly=True),
    ]


def test_a_worker_is_told_the_paths_of_every_other_task_outside_its_stack() -> None:
    tasks = [
        task_touching(1, ["src/app.py"]),
        task_touching(2, ["docs/guide.md (new)", "README.md"]),
        task_touching(3, ["src/app.py"]),
        task_touching(4, []),
    ]
    assert other_paths(tasks, tasks[0], {1, 3}) == ["#2: docs/guide.md, README.md"]
