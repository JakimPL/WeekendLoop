from __future__ import annotations

import pytest

from tests.unit.conftest import ELIGIBLE, build_assessment, build_task
from weekend_loop.models import Effort, Overlap, Risk, SoloReason, Task, TaskStatus, Verdict
from weekend_loop.waves import normalised_path, overlaps_within, solo_reason_of, waves_of

NO_SHARED_PATHS: list[str] = []


def task_touching(issue_number: int, paths: list[str]) -> Task:
    assessment = build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []).model_copy(
        update={"touched_paths": paths}
    )
    return build_task(
        issue_number, f"issue {issue_number}", TaskStatus.ASSESSED, ELIGIBLE, assessment
    )


def numbers_of(tasks: list[Task]) -> list[int]:
    return [task.issue_number for task in tasks]


def partition(tasks: list[Task], shared_paths: list[str]) -> list[list[int]]:
    return [numbers_of(wave.tasks) for wave in waves_of(tasks, shared_paths)]


def test_tasks_that_touch_the_same_file_take_different_waves() -> None:
    tasks = [task_touching(1, ["src/app.py"]), task_touching(2, ["src/app.py", "docs/a.md"])]
    assert partition(tasks, NO_SHARED_PATHS) == [[1], [2]]


def test_tasks_with_disjoint_paths_share_a_wave() -> None:
    tasks = [
        task_touching(1, ["src/app.py"]),
        task_touching(2, ["src/other.py"]),
        task_touching(3, ["docs/guide.md (new)"]),
    ]
    assert partition(tasks, NO_SHARED_PATHS) == [[1, 2, 3]]


def test_a_directory_covers_the_files_inside_it() -> None:
    assert partition(
        [task_touching(1, ["src"]), task_touching(2, ["src/app.py"])], NO_SHARED_PATHS
    ) == [[1], [2]]
    assert partition(
        [task_touching(1, ["src/app.py"]), task_touching(2, ["src/"])], NO_SHARED_PATHS
    ) == [[1], [2]]
    assert partition(
        [task_touching(1, ["source.py"]), task_touching(2, ["source"])], NO_SHARED_PATHS
    ) == [[1, 2]]


def test_a_task_that_touches_a_shared_path_runs_alone() -> None:
    tasks = [
        task_touching(1, ["src/app.py"]),
        task_touching(2, ["src/other.py", "CHANGELOG.md"]),
        task_touching(3, ["src/third.py"]),
    ]
    waves = waves_of(tasks, ["CHANGELOG.md"])
    assert [numbers_of(wave.tasks) for wave in waves] == [[1], [2], [3]]
    assert [wave.solo_reason for wave in waves] == [None, SoloReason.SHARED_PATH, None]


@pytest.mark.parametrize(
    ("touched", "pattern"),
    [
        ("docs/generated/table.md", "docs/generated/**"),
        ("docs/generated/table.md", "docs/generated"),
        ("docs", "docs/generated/**"),
        ("notes/CHANGELOG.md", "**/CHANGELOG.md"),
    ],
)
def test_a_shared_pattern_covers_what_a_path_would(touched: str, pattern: str) -> None:
    assert solo_reason_of(task_touching(1, [touched]), [pattern]) is SoloReason.SHARED_PATH
    assert solo_reason_of(task_touching(1, ["src/app.py"]), [pattern]) is None


def test_a_task_whose_assessment_names_no_path_runs_alone() -> None:
    tasks = [task_touching(1, ["src/app.py"]), task_touching(2, []), task_touching(3, ["x.py"])]
    waves = waves_of(tasks, NO_SHARED_PATHS)
    assert [numbers_of(wave.tasks) for wave in waves] == [[1], [2], [3]]
    assert waves[1].solo_reason is SoloReason.NO_TOUCHED_PATHS
    unassessed = build_task(4, "issue 4", TaskStatus.ASSESSED, ELIGIBLE, None)
    assert solo_reason_of(unassessed, NO_SHARED_PATHS) is SoloReason.NO_TOUCHED_PATHS


def test_the_execution_order_survives_the_partition() -> None:
    tasks = [
        task_touching(5, ["a.py"]),
        task_touching(2, ["b.py"]),
        task_touching(9, ["a.py"]),
        task_touching(1, []),
        task_touching(7, ["c.py"]),
        task_touching(3, ["d.py"]),
    ]
    waves = waves_of(tasks, NO_SHARED_PATHS)
    assert [numbers_of(wave.tasks) for wave in waves] == [[5, 2], [9], [1], [7, 3]]
    assert [number for wave in waves for number in numbers_of(wave.tasks)] == numbers_of(tasks)


def test_a_root_path_keeps_every_other_task_apart() -> None:
    tasks = [task_touching(1, ["src/app.py"]), task_touching(2, ["."]), task_touching(3, ["x"])]
    assert partition(tasks, NO_SHARED_PATHS) == [[1], [2], [3]]


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


def test_two_tasks_that_changed_the_same_file_are_paired_unless_the_file_is_shared() -> None:
    changed = {1: ["a.py", "CHANGELOG.md"], 2: ["a.py", "CHANGELOG.md"], 3: ["b.py"]}
    assert overlaps_within(changed, ["CHANGELOG.md"]) == {
        1: [Overlap(issue_number=2, paths=["a.py"])],
        2: [Overlap(issue_number=1, paths=["a.py"])],
    }
    assert overlaps_within({1: ["CHANGELOG.md"], 2: ["CHANGELOG.md"]}, ["CHANGELOG.md"]) == {}
    assert overlaps_within({1: ["a.py"], 2: ["b.py"]}, NO_SHARED_PATHS) == {}


def test_every_pair_of_a_wave_is_compared() -> None:
    changed = {1: ["a.py"], 2: ["b.py"], 3: ["a.py", "b.py"]}
    found = overlaps_within(changed, NO_SHARED_PATHS)
    assert found[3] == [
        Overlap(issue_number=1, paths=["a.py"]),
        Overlap(issue_number=2, paths=["b.py"]),
    ]
    assert found[1] == [Overlap(issue_number=3, paths=["a.py"])]
