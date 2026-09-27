from __future__ import annotations

from pathlib import PurePosixPath
from typing import Final

from weekend_loop.gate import path_matches
from weekend_loop.models import Record, SoloReason, Task

NEW_PATH_MARKER: Final[str] = "(new)"
REPOSITORY_ROOT: Final[str] = ""
CURRENT_DIRECTORY: Final[str] = "."
QUOTE_CHARACTERS: Final[str] = "`'\""


class Wave(Record):
    tasks: list[Task]
    solo_reason: SoloReason | None


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


def overlapping(paths: list[str], others: list[str]) -> bool:
    return any(overlap(path, other) for path in paths for other in others)


def shared(path: str, pattern: str) -> bool:
    return path_matches(path, pattern) or overlap(path, normalised_path(pattern))


def solo_reason_of(task: Task, shared_paths: list[str]) -> SoloReason | None:
    paths = touched_paths_of(task)
    if not paths:
        return SoloReason.NO_TOUCHED_PATHS
    if any(shared(path, pattern) for path in paths for pattern in shared_paths):
        return SoloReason.SHARED_PATH
    return None


def closed(waves: list[Wave], members: list[Task]) -> list[Wave]:
    return [*waves, Wave(tasks=members, solo_reason=None)] if members else waves


def waves_of(tasks: list[Task], shared_paths: list[str]) -> list[Wave]:
    waves: list[Wave] = []
    members: list[Task] = []
    claimed: list[str] = []
    for task in tasks:
        reason = solo_reason_of(task, shared_paths)
        paths = touched_paths_of(task)
        if reason is not None or overlapping(paths, claimed):
            waves = closed(waves, members)
            members, claimed = [], []
        if reason is not None:
            waves.append(Wave(tasks=[task], solo_reason=reason))
            continue
        members.append(task)
        claimed.extend(paths)
    return closed(waves, members)
