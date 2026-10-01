from __future__ import annotations

from pathlib import PurePosixPath
from typing import Final

from weekend_loop.gate import path_matches
from weekend_loop.models import Overlap, Task

NEW_PATH_MARKER: Final[str] = "(new)"
REPOSITORY_ROOT: Final[str] = ""
CURRENT_DIRECTORY: Final[str] = "."
QUOTE_CHARACTERS: Final[str] = "`'\""

type Pair = tuple[int, int]


def normalised_path(path: str) -> str:
    stripped = path.strip().strip(QUOTE_CHARACTERS).strip()
    if stripped.endswith(NEW_PATH_MARKER):
        stripped = stripped[: -len(NEW_PATH_MARKER)].rstrip()
    posix = PurePosixPath(stripped.strip(QUOTE_CHARACTERS).strip().lstrip("/")).as_posix()
    return REPOSITORY_ROOT if posix == CURRENT_DIRECTORY else posix


def touched_paths_of(task: Task) -> list[str]:
    if task.assessment is None:
        return []
    return [normalised_path(path) for path in task.assessment.touched_paths if path.strip()]


def overlap(path: str, other: str) -> bool:
    if path == REPOSITORY_ROOT or other == REPOSITORY_ROOT:
        return True
    return path == other or other.startswith(f"{path}/") or path.startswith(f"{other}/")


def shared(path: str, pattern: str) -> bool:
    return path_matches(path, pattern) or overlap(path, normalised_path(pattern))


def hand_written(paths: list[str], shared_paths: list[str]) -> set[str]:
    return {path for path in paths if not any(shared(path, pattern) for pattern in shared_paths)}


def pairs_sharing_files(
    changed: dict[int, list[str]], shared_paths: list[str], excluded: set[frozenset[int]]
) -> dict[Pair, list[str]]:
    numbers = sorted(changed)
    sharing: dict[Pair, list[str]] = {}
    for position, first in enumerate(numbers):
        for second in numbers[position + 1 :]:
            if frozenset((first, second)) in excluded:
                continue
            common = sorted(
                hand_written(changed[first], shared_paths)
                & hand_written(changed[second], shared_paths)
            )
            if common:
                sharing[(first, second)] = common
    return sharing


def overlaps_found(
    sharing: dict[Pair, list[str]], conflicts: dict[Pair, list[str]]
) -> dict[int, list[Overlap]]:
    found: dict[int, list[Overlap]] = {}
    for (first, second), common in sharing.items():
        conflicted = sorted(set(conflicts.get((first, second), [])) & set(common))
        paths = conflicted or common
        clean = not conflicted
        found.setdefault(first, []).append(
            Overlap(issue_number=second, paths=paths, merges_cleanly=clean)
        )
        found.setdefault(second, []).append(
            Overlap(issue_number=first, paths=paths, merges_cleanly=clean)
        )
    return found


def other_paths(tasks: list[Task], task: Task, same_stack: set[int]) -> list[str]:
    return [
        f"#{other.issue_number}: {', '.join(touched_paths_of(other))}"
        for other in tasks
        if other.issue_number != task.issue_number
        and other.issue_number not in same_stack
        and touched_paths_of(other)
    ]
